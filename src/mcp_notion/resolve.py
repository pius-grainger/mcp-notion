"""A caller's `ref` -> a Notion UUID, with an in-process cache.

A ref is tried as an alias, then a URL, then a bare UUID, then an exact title.
Ambiguity is always an error carrying candidates; the server never guesses
which page the caller meant.
"""

import os
import re

from . import format as fmt
from .client import NotionClient

_ALIAS_PREFIX = "NOTION_ALIAS_"
_UUID = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{8})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{12})(?![0-9a-fA-F])")


class ResolutionError(Exception):
    """Raised when a ref maps to zero or more than one Notion entity.

    Caught at the tool boundary in server.py; never crosses the MCP boundary.
    """

    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.candidates = candidates or []


def aliases() -> dict[str, str]:
    """Configured NOTION_ALIAS_* variables, keyed by lowercased alias name."""
    return {
        name[len(_ALIAS_PREFIX) :].lower(): value
        for name, value in os.environ.items()
        if name.startswith(_ALIAS_PREFIX) and value
    }


def extract_id(ref: str | None) -> str | None:
    """Dashed UUID from a bare id or a notion.so URL. None if the ref is a title.

    The query string is stripped first: a `?v=` view id is a valid-looking UUID
    and would otherwise win over the page id.
    """
    candidate = (ref or "").strip().split("?", 1)[0]
    if not candidate:
        return None
    matches = _UUID.findall(candidate)
    if not matches:
        return None
    # The page id is the last id in the path: /workspace/Title-<id>.
    return "-".join(matches[-1]).lower()


MAX_CANDIDATES = 5
_SHARING_HINT = (
    "If it exists, open it in Notion and share it with your integration "
    "(••• menu > Connections)."
)


class Resolver:
    def __init__(self, client: NotionClient):
        self._client = client
        # Ref -> UUID only. Page and row content is never cached; a restart is
        # the invalidation mechanism. Failures are deliberately not cached, so a
        # newly shared page resolves on the next call rather than staying broken.
        self._cache: dict[tuple[str, str], str] = {}

    def database(self, ref: str) -> str:
        return self._resolve(ref, "database")

    def page(self, ref: str) -> str:
        return self._resolve(ref, "page")

    def any(self, ref: str) -> tuple[str, str]:
        """("database" | "page", uuid). create_page accepts either as a parent."""
        resolved = self._direct(ref)
        if resolved:
            body = self._client.request("GET", f"/databases/{resolved}")
            if "error" not in body:
                return ("database", resolved)
            if body.get("status") == 404:
                # A genuinely unshared id also 404s here; that surfaces as a
                # clear error at the create call, which is fine. But anything
                # else (bad token, rate limit, network failure) is not "not a
                # database" — it is a client error, and misreporting it as a
                # page would send create_page a wrong-shaped parent payload.
                return ("page", resolved)
            raise ResolutionError(body["error"])
        try:
            return ("database", self._resolve(ref, "database"))
        except ResolutionError as database_error:
            try:
                return ("page", self._resolve(ref, "page"))
            except ResolutionError as page_error:
                raise _both_branches_failed(ref, database_error, page_error) from None

    def search(self, query: str, kind: str | None = None, max_items: int | None = None) -> list[dict]:
        """Raw search nodes. Raises ResolutionError if the request itself fails.

        `max_items` caps the fetch. Title resolution leaves it unset on purpose:
        it must see every match to tell one from many.
        """
        payload: dict = {"query": query or ""}
        if kind:
            payload["filter"] = {"property": "object", "value": kind}
        body = self._client.paginate("POST", "/search", payload, max_items=max_items)
        if "error" in body:
            raise ResolutionError(body["error"])
        return body["results"]

    def _direct(self, ref: str) -> str | None:
        """Alias or id/URL, whichever hits. None means the ref is a title."""
        alias = aliases().get((ref or "").strip().lower())
        return extract_id(alias) if alias else extract_id(ref)

    def _resolve(self, ref: str, kind: str) -> str:
        if not (ref or "").strip():
            raise ResolutionError("A page or database reference is required.")

        resolved = self._direct(ref)
        if resolved:
            return resolved

        key = (kind, ref.strip().lower())
        if key in self._cache:
            return self._cache[key]

        nodes = self.search(ref, kind)
        wanted = ref.strip().casefold()
        exact = [node for node in nodes if fmt.entity_title(node).strip().casefold() == wanted]

        if len(exact) == 1:
            resolved = extract_id(exact[0].get("id"))
            self._cache[key] = resolved
            return resolved

        if len(exact) > 1:
            raise ResolutionError(
                f"'{ref}' matches {len(exact)} {kind}s. Pass the URL of the one you mean.",
                _candidates(exact),
            )

        if nodes:
            raise ResolutionError(
                f"No {kind} is titled exactly '{ref}'. Pass the URL of the one you mean.",
                _candidates(nodes),
            )

        raise ResolutionError(f"No {kind} named '{ref}' is visible to this integration. {_SHARING_HINT}")


def _candidates(nodes: list[dict]) -> list[dict]:
    return [{"title": fmt.entity_title(node), "url": node.get("url")} for node in nodes[:MAX_CANDIDATES]]


def _both_branches_failed(
    ref: str, database_error: ResolutionError, page_error: ResolutionError
) -> ResolutionError:
    """Pick the error Resolver.any should raise when neither branch resolved.

    The database branch runs first, so reporting only the page branch's failure
    throws away the databases the first branch found and points the user at the
    sharing menu for a sharing problem they do not have. Candidates win; when
    both branches found some, both lists are shown.
    """
    if database_error.candidates and not page_error.candidates:
        return database_error
    if database_error.candidates and page_error.candidates:
        return ResolutionError(
            f"'{ref}' does not name exactly one page or database. "
            "Pass the URL of the one you mean.",
            _dedupe(database_error.candidates + page_error.candidates),
        )
    # Neither branch found a candidate. The page branch's message is the more
    # useful of the two identical shapes, and if the search request itself
    # failed (bad token, rate limit) it carries that message verbatim.
    return page_error


def _dedupe(candidates: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for candidate in candidates:
        key = (candidate.get("title"), candidate.get("url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out[:MAX_CANDIDATES]
