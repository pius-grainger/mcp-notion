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
            return {"error": _error_message(response)}

        try:
            body = response.json()
        except ValueError:
            return {"error": f"Notion returned a non-JSON response: {response.text[:200]}"}
        if not isinstance(body, dict):
            return {"error": f"Notion returned an unexpected response shape: {body!r:.200}"}
        return body

    def paginate(self, method: str, path: str, json: dict | None = None) -> dict:
        """Follow `next_cursor` to exhaustion. Returns {"results": [...]} or an error."""
        results: list[dict] = []
        cursor: str | None = None
        while True:
            payload = dict(json or {})
            payload["page_size"] = PAGE_SIZE
            if cursor:
                payload["start_cursor"] = cursor
            if method.upper() == "GET":
                body = self.request(method, path, params=payload)
            else:
                body = self.request(method, path, json=payload)
            if "error" in body:
                return body
            results.extend(body.get("results") or [])
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
            children = self.fetch_block_tree(block["id"], max_depth - 1)
            if isinstance(children, dict):
                return children
            if children:
                block["children"] = children
        return blocks

    def append_blocks(self, block_id: str, blocks: list[dict]) -> dict:
        """Append in chunks of 100. Notion rejects more in one request."""
        for start in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST):
            chunk = blocks[start : start + MAX_BLOCKS_PER_REQUEST]
            body = self.request("PATCH", f"/blocks/{block_id}/children", json={"children": chunk})
            if "error" in body:
                return body
        return {"appended": len(blocks)}

    def _send(self, method: str, path: str, json: dict | None, params: dict | None) -> httpx.Response | dict:
        try:
            return self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as e:
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
