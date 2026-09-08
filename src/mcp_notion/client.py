"""Notion REST transport. The only module that touches the network."""

import time

import httpx

NOTION_API_URL = "https://api.notion.com/v1"
# Pinned deliberately. 2025-09-03 splits databases into data sources and moves
# the query endpoint; that indirection buys nothing here.
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS_PER_REQUEST = 100
PAGE_SIZE = 100
MAX_RETRY_WAIT = 10.0


class NotionClient:
    def __init__(self, api_key: str, url: str = NOTION_API_URL, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def request(self, method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict:
        """Return the parsed response body, or a dict with an "error" key."""
        response = self._send(method, path, json, params)
        if isinstance(response, dict):
            return response

        if response.status_code == 429:
            # One retry. Notion's Retry-After is in seconds; cap it so a long
            # header value cannot stall a tool call past the client timeout.
            wait = _retry_after(response)
            if wait is not None:
                time.sleep(wait)
                response = self._send(method, path, json, params)
                if isinstance(response, dict):
                    return response

        if response.status_code >= 400:
            return {"error": _error_message(response), "status": response.status_code}

        try:
            body = response.json()
        except ValueError:
            return {"error": f"Notion returned a non-JSON response: {response.text[:200]}", "status": response.status_code}
        if not isinstance(body, dict):
            return {
                "error": f"Notion returned an unexpected response shape: {body!r:.200}",
                "status": response.status_code,
            }
        return body

    def paginate(self, method: str, path: str, json: dict | None = None, max_items: int | None = None) -> dict:
        """Follow `next_cursor`. Returns {"results": [...]} or an error.

        Without `max_items` every cursor is followed to exhaustion, which is what
        a block tree or a full database listing needs. With it, the fetch stops
        as soon as that many results are in hand and sizes the request to match:
        returning 25 rows from a 2000-row database is one request, not twenty.
        """
        if max_items is not None and max_items <= 0:
            return {"results": []}
        results: list[dict] = []
        cursor: str | None = None
        page_size = PAGE_SIZE if max_items is None else min(max_items, PAGE_SIZE)
        while True:
            payload = dict(json or {})
            payload["page_size"] = page_size
            if cursor:
                payload["start_cursor"] = cursor
            if method.upper() == "GET":
                body = self.request(method, path, params=payload)
            else:
                body = self.request(method, path, json=payload)
            if "error" in body:
                return body
            results.extend(body.get("results") or [])
            if max_items is not None and len(results) >= max_items:
                return {"results": results[:max_items]}
            if not body.get("has_more"):
                return {"results": results}
            cursor = body.get("next_cursor")
            if not cursor:
                # has_more without a cursor would loop forever.
                return {"results": results}

    def fetch_block_tree(self, block_id: str, max_depth: int = 3) -> list[dict] | dict:
        """Blocks under `block_id`, with nested children attached under "children"."""
        if max_depth <= 0:
            return []
        body = self.paginate("GET", f"/blocks/{block_id}/children")
        if "error" in body:
            return body
        blocks = body["results"]
        for block in blocks:
            if not block.get("has_children"):
                continue
            child_id = block.get("id")
            if not child_id:
                # A block without an id cannot be followed. Notion always sends
                # one; a missing key is a response shape we do not understand,
                # and dropping the children beats raising out of the tool.
                continue
            children = self.fetch_block_tree(child_id, max_depth - 1)
            if isinstance(children, dict):
                return children
            if children:
                block["children"] = children
        return blocks

    def append_blocks(self, block_id: str, blocks: list[dict]) -> dict:
        """Append in chunks of 100. Notion rejects more in one request.

        A failure part-way through is a partial write: the chunks before it are
        already in Notion and there is no undo. "appended" is therefore reported
        on the error path too, so the caller learns how much landed instead of
        retrying the whole body and duplicating it.
        """
        appended = 0
        for start in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST):
            chunk = blocks[start : start + MAX_BLOCKS_PER_REQUEST]
            body = self.request("PATCH", f"/blocks/{block_id}/children", json={"children": chunk})
            if "error" in body:
                return {**body, "appended": appended}
            appended += len(chunk)
        return {"appended": appended}

    def _send(self, method: str, path: str, json: dict | None, params: dict | None) -> httpx.Response | dict:
        try:
            return self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as e:
            # No HTTP response was ever received, so there is no status code to
            # report. The "status" key is omitted entirely here (never set to
            # None) so callers can use a plain `.get("status") == 404` check.
            return {"error": f"Notion request failed: {e}"}


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        wait = float(raw) if raw is not None else 1.0
    except ValueError:
        wait = 1.0
    return min(max(wait, 0.0), MAX_RETRY_WAIT)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = body.get("message") if isinstance(body, dict) else None
    message = message or response.text[:200]

    if response.status_code == 401:
        return f"Notion rejected the API token (401): {message}"
    if response.status_code in (403, 404):
        # The single most common Notion failure: the id is fine, the integration
        # simply has not been given access. A bare "not found" sends the user
        # hunting for a typo that is not there.
        return (
            f"Notion returned {response.status_code}: {message} "
            "If the id is correct, open the page in Notion and share it with "
            "your integration (••• menu > Connections)."
        )
    if response.status_code == 429:
        return f"Notion rate limit reached (429): {message}"
    return f"Notion API error {response.status_code}: {message}"
