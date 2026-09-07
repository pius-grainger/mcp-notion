"""A caller's `ref` -> a Notion UUID, with an in-process cache.

A ref is tried as an alias, then a URL, then a bare UUID, then an exact title.
Ambiguity is always an error carrying candidates; the server never guesses
which page the caller meant.
"""

import os
import re

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
