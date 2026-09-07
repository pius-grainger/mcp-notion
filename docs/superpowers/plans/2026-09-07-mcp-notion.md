# mcp-notion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone MCP server exposing eight narrow Notion tools (five read, three additive-only write) backed by the Notion REST API.

**Architecture:** A thin `NotionClient` speaks REST over `httpx` and is the only module that touches the network; it also owns pagination, the 429 retry, and 100-block append chunking. `markdown.py` converts between Notion blocks and a documented markdown subset. `properties.py` translates Notion's typed property values to and from plain Python and builds query filters from the database schema. `format.py` flattens nodes into dicts that carry a `url` and never a bare UUID. `Resolver` turns a caller's `ref` — alias, URL, UUID, or title — into a UUID. `server.py` declares the FastMCP tools. No exception crosses the MCP boundary.

**Tech Stack:** Python 3.11+, `mcp[cli]` (FastMCP), `httpx`, `python-dotenv`, `pytest`, `respx`.

**Spec:** `docs/superpowers/specs/2026-09-07-mcp-notion-design.md`

## Global Constraints

- Python `>=3.11`. Build backend: `hatchling`. Package lives at `src/mcp_notion`.
- Runtime deps, exact floors: `mcp[cli]>=1.0.0,<2.0.0`, `httpx>=0.27.0`, `python-dotenv>=1.0.0`. Dev deps: `pytest>=8.0.0`, `respx>=0.21.0`. Add nothing else — no markdown library, no Notion SDK.
- Base URL: `https://api.notion.com/v1`. Timeout: 30s.
- Every request sends three headers: `Authorization: Bearer <key>`, `Notion-Version: 2022-06-28`, `Content-Type: application/json`. The version header is **required**; Notion rejects requests without it.
- `NOTION_VERSION` is pinned to the literal string `"2022-06-28"`. Do not use `2025-09-03` — it splits databases into data sources and moves the query endpoint.
- API key comes from `NOTION_API_KEY`, read lazily on first tool call. Missing key raises `RuntimeError("NOTION_API_KEY environment variable is not set")`.
- Notion signals failure with a real HTTP status code and a body of the shape `{"object": "error", "status": 404, "code": "object_not_found", "message": "..."}`.
- No exception crosses the MCP boundary. Tools returning a dict return `{"error": str}` on failure; tools returning a list return `[{"error": str}]`.
- No bare Notion UUID appears in any tool output. Every returned entity carries a `url`, which is itself a valid `ref` for a later call.
- Writes are additive only. Never implement page archiving, block deletion, or page-body replacement.
- Notion limits, enforced by the server, never by the caller: 100 blocks per `PATCH /v1/blocks/{id}/children` request; 2000 characters per `rich_text` object; `page_size` maximum 100 on every paginated endpoint.
- Nested block recursion is capped at depth 3.
- Never write a real API key into `.env.example`, the README, a test, or any other committed file.
- The test suite runs with no `NOTION_API_KEY` set and no network access. Every test that would hit the network uses `respx`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Package metadata, deps, `mcp-notion` console script |
| `.env.example` | `NOTION_API_KEY` and an example `NOTION_ALIAS_*` line, both with placeholder values |
| `README.md` | Setup, integration sharing, alias config, tool reference |
| `src/mcp_notion/__init__.py` | Empty package marker |
| `src/mcp_notion/client.py` | `NotionClient` — transport, auth, version header, error mapping, pagination, 429 retry, block-tree fetch, append chunking. Only module touching the network |
| `src/mcp_notion/markdown.py` | Blocks ↔ markdown subset. Pure. Owns `rich_text` construction and the 2000-char split |
| `src/mcp_notion/properties.py` | Notion property values ↔ plain Python; filter and sort construction from the schema. Pure |
| `src/mcp_notion/format.py` | Page, database, and schema nodes → flat dicts. Pure |
| `src/mcp_notion/resolve.py` | `Resolver` — alias/URL/UUID parsing, title search, ambiguity errors, cache |
| `src/mcp_notion/server.py` | FastMCP instance, eight `@mcp.tool()` definitions, `main()` |
| `tests/test_smoke.py` | Server imports, name, registered tool names |
| `tests/test_client.py` | Transport, headers, error mapping, pagination, retry, chunking |
| `tests/test_markdown.py` | Round-trips, unsupported handling, text splitting |
| `tests/test_properties.py` | Read and write per property type, filters, sorts |
| `tests/test_format.py` | Node flattening, no mocks |
| `tests/test_resolve.py` | Ref parsing, search hit/ambiguous/miss, cache |
| `tests/test_tools.py` | Per-tool happy path and failure path |

---

### Task 1: Scaffolding and live API probe

The probe runs first for the same reason `mcp-linear`'s did: once a mocked test hardcodes a response shape, a wrong shape is invisible until production. Confirm the real shapes against a real workspace before any mock exists. Scaffolding ships with the probe because the probe needs an installed package to import from.

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore` (already present — verify), `src/mcp_notion/__init__.py`, `src/mcp_notion/server.py`
- Create: `tests/test_smoke.py`
- Create: `scripts/probe_api.py` (throwaway — deleted in Step 7)

**Interfaces:**
- Consumes: nothing
- Produces: `mcp_notion.server.mcp` (a `FastMCP` instance named `"Notion"`), `mcp_notion.server.main() -> None`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mcp-notion"
version = "0.1.0"
description = "MCP server exposing narrow Notion page and database operations"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0,<2.0.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "respx>=0.21.0",
]

[project.scripts]
mcp-notion = "mcp_notion.server:main"

[tool.hatch.build.targets.wheel]
packages = ["src/mcp_notion"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write the package skeleton**

`src/mcp_notion/__init__.py` is empty. `src/mcp_notion/server.py`:

```python
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP(
    "Notion",
    instructions=(
        "Reads and writes Notion pages and databases. Pages and databases are "
        "addressed by a `ref`: a configured alias, a notion.so URL, a page id, "
        "or an exact title. Call list_databases to discover aliases and "
        "get_database_schema before filtering or writing properties. Page "
        "content is markdown. Writes are additive: nothing is deleted, "
        "archived, or overwritten."
    ),
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write the smoke test**

`tests/test_smoke.py`:

```python
from mcp_notion import server


def test_server_exposes_a_named_mcp_instance():
    assert server.mcp.name == "Notion"


def test_main_is_callable():
    assert callable(server.main)
```

- [ ] **Step 4: Install and run the smoke test**

Run:
```bash
cd /Users/piuschungath/Workspace/mcp-notion
python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
.venv/bin/pytest tests/test_smoke.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Write `.env.example`**

```
NOTION_API_KEY=ntn_your_integration_token_here
NOTION_ALIAS_TASKS=https://www.notion.so/workspace/0123456789abcdef0123456789abcdef
```

- [ ] **Step 6: Write and run the probe**

`scripts/probe_api.py`:

```python
"""Throwaway. Prints real Notion response shapes so mocked tests match reality."""

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY = os.environ.get("NOTION_API_KEY")
if not KEY:
    sys.exit("NOTION_API_KEY is not set")

client = httpx.Client(
    base_url="https://api.notion.com/v1",
    headers={
        "Authorization": f"Bearer {KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    },
    timeout=30.0,
)


def show(label, response):
    print(f"\n===== {label} -> {response.status_code} =====")
    print(json.dumps(response.json(), indent=2)[:4000])


show("search pages", client.post("/search", json={"filter": {"property": "object", "value": "page"}, "page_size": 3}))
show("search databases", client.post("/search", json={"filter": {"property": "object", "value": "database"}, "page_size": 3}))

db_id = sys.argv[1] if len(sys.argv) > 1 else None
if db_id:
    show("database schema", client.get(f"/databases/{db_id}"))
    show("database query", client.post(f"/databases/{db_id}/query", json={"page_size": 3}))

page_id = sys.argv[2] if len(sys.argv) > 2 else None
if page_id:
    show("page", client.get(f"/pages/{page_id}"))
    show("blocks", client.get(f"/blocks/{page_id}/children", params={"page_size": 20}))

show("bad id", client.get("/pages/00000000000000000000000000000000"))
```

Run:
```bash
.venv/bin/python scripts/probe_api.py <database_id> <page_id>
```

Read the output and confirm, before writing any other task: the property-value key names per type, the `rich_text` object shape including `annotations` and `href`, the block payload key per block type, the pagination keys (`results`, `next_cursor`, `has_more`), and the error body shape from the bad-id call. Where the probe contradicts a shape written in a later task, the probe wins — correct the plan.

- [ ] **Step 7: Delete the probe and commit**

```bash
cd /Users/piuschungath/Workspace/mcp-notion
rm -rf scripts
git add pyproject.toml .env.example .gitignore src tests
git commit -m "feat: scaffold mcp-notion package and smoke test"
```

The probe is deleted deliberately: it is throwaway, it only runs against a live workspace, and a committed script that needs a real token invites one being pasted into it.

---

### Task 2: NotionClient transport

**Files:**
- Create: `src/mcp_notion/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `NOTION_API_URL = "https://api.notion.com/v1"`, `NOTION_VERSION = "2022-06-28"`, `MAX_BLOCKS_PER_REQUEST = 100`, `PAGE_SIZE = 100`
  - `NotionClient(api_key: str, url: str = NOTION_API_URL, timeout: float = 30.0)`
  - `NotionClient.request(method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict` — parsed body, or `{"error": str}`
  - `NotionClient.paginate(method: str, path: str, json: dict | None = None) -> dict` — `{"results": [...]}` or `{"error": str}`
  - `NotionClient.fetch_block_tree(block_id: str, max_depth: int = 3) -> list[dict] | dict` — blocks with a `"children"` key where nested, or `{"error": str}`
  - `NotionClient.append_blocks(block_id: str, blocks: list[dict]) -> dict` — `{"appended": int}` or `{"error": str}`

- [ ] **Step 1: Write the failing tests**

`tests/test_client.py`:

```python
import json

import httpx
import pytest
import respx

from mcp_notion.client import NOTION_API_URL, NOTION_VERSION, NotionClient


@pytest.fixture
def client():
    return NotionClient(api_key="ntn_test")


@respx.mock
def test_request_returns_the_parsed_body(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(200, json={"object": "page", "id": "abc"})
    )
    assert client.request("GET", "/pages/abc") == {"object": "page", "id": "abc"}


@respx.mock
def test_request_sends_bearer_token_and_version_header(client):
    route = respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc"})
    )
    client.request("GET", "/pages/abc")
    headers = route.calls.last.request.headers
    assert headers["authorization"] == "Bearer ntn_test"
    assert headers["notion-version"] == NOTION_VERSION


@respx.mock
def test_request_maps_401_to_an_invalid_token_error(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(
            401, json={"object": "error", "status": 401, "code": "unauthorized", "message": "API token is invalid."}
        )
    )
    assert "token" in client.request("GET", "/pages/abc")["error"].lower()


@respx.mock
def test_request_maps_404_to_a_sharing_hint(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(
            404,
            json={"object": "error", "status": 404, "code": "object_not_found", "message": "Could not find page."},
        )
    )
    error = client.request("GET", "/pages/abc")["error"]
    assert "share" in error.lower() and "integration" in error.lower()


@respx.mock
def test_request_maps_403_to_a_sharing_hint(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(
            403, json={"object": "error", "status": 403, "code": "restricted_resource", "message": "No access."}
        )
    )
    error = client.request("GET", "/pages/abc")["error"]
    assert "share" in error.lower() and "integration" in error.lower()


@respx.mock
def test_request_surfaces_a_400_message_verbatim(client):
    respx.patch(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(
            400,
            json={"object": "error", "status": 400, "code": "validation_error", "message": "body.properties.Status is not a select."},
        )
    )
    assert "body.properties.Status is not a select." in client.request("PATCH", "/pages/abc", json={})["error"]


@respx.mock
def test_request_retries_once_on_429_then_succeeds(client):
    route = respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"}),
            httpx.Response(200, json={"id": "abc"}),
        ]
    )
    assert client.request("GET", "/pages/abc") == {"id": "abc"}
    assert route.call_count == 2


@respx.mock
def test_request_errors_after_a_second_429(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"})
    )
    assert "rate" in client.request("GET", "/pages/abc")["error"].lower()


@respx.mock
def test_request_reports_a_non_json_body(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    assert "non-JSON" in client.request("GET", "/pages/abc")["error"]


def test_request_reports_a_transport_failure(client):
    with respx.mock:
        respx.get(f"{NOTION_API_URL}/pages/abc").mock(side_effect=httpx.ConnectError("boom"))
        assert "failed" in client.request("GET", "/pages/abc")["error"].lower()


@respx.mock
def test_paginate_follows_the_cursor(client):
    respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"id": "1"}], "has_more": True, "next_cursor": "cur"}),
            httpx.Response(200, json={"results": [{"id": "2"}], "has_more": False, "next_cursor": None}),
        ]
    )
    assert client.paginate("POST", "/databases/db/query") == {"results": [{"id": "1"}, {"id": "2"}]}


@respx.mock
def test_paginate_sends_the_cursor_on_the_second_request(client):
    route = respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        side_effect=[
            httpx.Response(200, json={"results": [], "has_more": True, "next_cursor": "cur"}),
            httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None}),
        ]
    )
    client.paginate("POST", "/databases/db/query")
    assert json.loads(route.calls[1].request.content)["start_cursor"] == "cur"


@respx.mock
def test_paginate_propagates_an_error(client):
    respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        return_value=httpx.Response(401, json={"object": "error", "message": "bad token"})
    )
    assert "error" in client.paginate("POST", "/databases/db/query")


@respx.mock
def test_append_blocks_chunks_at_one_hundred(client):
    route = respx.patch(f"{NOTION_API_URL}/blocks/abc/children").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    blocks = [{"object": "block", "type": "divider", "divider": {}} for _ in range(150)]
    assert client.append_blocks("abc", blocks) == {"appended": 150}
    assert route.call_count == 2
    assert len(json.loads(route.calls[0].request.content)["children"]) == 100
    assert len(json.loads(route.calls[1].request.content)["children"]) == 50


@respx.mock
def test_append_blocks_stops_on_the_first_error(client):
    route = respx.patch(f"{NOTION_API_URL}/blocks/abc/children").mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(400, json={"object": "error", "message": "bad block"}),
        ]
    )
    blocks = [{"object": "block", "type": "divider", "divider": {}} for _ in range(150)]
    assert "error" in client.append_blocks("abc", blocks)
    assert route.call_count == 2


@respx.mock
def test_fetch_block_tree_attaches_children(client):
    respx.get(f"{NOTION_API_URL}/blocks/root/children").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "kid", "type": "bulleted_list_item", "has_children": True}], "has_more": False, "next_cursor": None},
        )
    )
    respx.get(f"{NOTION_API_URL}/blocks/kid/children").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "grandkid", "type": "paragraph", "has_children": False}], "has_more": False, "next_cursor": None},
        )
    )
    tree = client.fetch_block_tree("root")
    assert tree[0]["children"][0]["id"] == "grandkid"


@respx.mock
def test_fetch_block_tree_stops_at_max_depth(client):
    respx.get(f"{NOTION_API_URL}/blocks/root/children").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "kid", "type": "paragraph", "has_children": True}], "has_more": False, "next_cursor": None},
        )
    )
    kid = respx.get(f"{NOTION_API_URL}/blocks/kid/children")
    tree = client.fetch_block_tree("root", max_depth=1)
    assert "children" not in tree[0]
    assert kid.call_count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_client.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_notion.client'`.

- [ ] **Step 3: Write `src/mcp_notion/client.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_client.py -v`
Expected: all pass.

If the `paginate` or `fetch_block_tree` tests fail with an unmatched-route error, it is because `paginate` sends `page_size` as a query parameter on GET requests while the respx pattern declares no query string. Do not change the client: relax the pattern instead, e.g. `respx.get(url__regex=rf"{NOTION_API_URL}/blocks/root/children.*")`. The same applies to the `/blocks/.../children` routes in `tests/test_tools.py`.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/client.py tests/test_client.py
git commit -m "feat: add Notion REST client with pagination, retry, and chunking"
```

---

### Task 3: Blocks to markdown

**Files:**
- Create: `src/mcp_notion/markdown.py`
- Test: `tests/test_markdown.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `MAX_TEXT_LENGTH = 2000`
  - `plain_text(rich: list[dict] | None) -> str` — annotations dropped, used by `format.py`
  - `blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_markdown.py`:

```python
from mcp_notion.markdown import blocks_to_markdown, plain_text


def text(content, **annotations):
    return {
        "type": "text",
        "text": {"content": content, "link": annotations.pop("link", None)},
        "annotations": {"bold": False, "italic": False, "code": False, **annotations},
        "plain_text": content,
    }


def block(kind, **payload):
    return {"object": "block", "type": kind, "has_children": False, kind: payload}


def test_plain_text_joins_and_drops_annotations():
    assert plain_text([text("a"), text("b", bold=True)]) == "ab"


def test_plain_text_tolerates_none():
    assert plain_text(None) == ""


def test_paragraph():
    assert blocks_to_markdown([block("paragraph", rich_text=[text("hello")])]) == "hello"


def test_headings():
    blocks = [
        block("heading_1", rich_text=[text("one")]),
        block("heading_2", rich_text=[text("two")]),
        block("heading_3", rich_text=[text("three")]),
    ]
    assert blocks_to_markdown(blocks) == "# one\n\n## two\n\n### three"


def test_bulleted_and_numbered_lists():
    blocks = [
        block("bulleted_list_item", rich_text=[text("a")]),
        block("numbered_list_item", rich_text=[text("b")]),
    ]
    assert blocks_to_markdown(blocks) == "- a\n\n1. b"


def test_to_do_reflects_checked_state():
    blocks = [
        block("to_do", rich_text=[text("open")], checked=False),
        block("to_do", rich_text=[text("done")], checked=True),
    ]
    assert blocks_to_markdown(blocks) == "- [ ] open\n\n- [x] done"


def test_code_block_carries_its_language():
    result = blocks_to_markdown([block("code", rich_text=[text("x = 1")], language="python")])
    assert result == "```python\nx = 1\n```"


def test_quote_and_divider():
    blocks = [block("quote", rich_text=[text("said")]), block("divider")]
    assert blocks_to_markdown(blocks) == "> said\n\n---"


def test_inline_annotations_render_as_markdown():
    rich = [text("b", bold=True), text("i", italic=True), text("c", code=True)]
    assert blocks_to_markdown([block("paragraph", rich_text=rich)]) == "**b***i*`c`"


def test_link_renders_as_a_markdown_link():
    rich = [text("site", link={"url": "https://example.com"})]
    assert blocks_to_markdown([block("paragraph", rich_text=rich)]) == "[site](https://example.com)"


def test_nested_children_are_indented():
    parent = block("bulleted_list_item", rich_text=[text("outer")])
    parent["has_children"] = True
    parent["children"] = [block("bulleted_list_item", rich_text=[text("inner")])]
    assert blocks_to_markdown([parent]) == "- outer\n\n  - inner"


def test_unsupported_block_becomes_a_visible_placeholder():
    assert blocks_to_markdown([block("image", file={})]) == "<!-- unsupported: image -->"


def test_empty_block_list_is_an_empty_string():
    assert blocks_to_markdown([]) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_notion.markdown'`.

- [ ] **Step 3: Write the read half of `src/mcp_notion/markdown.py`**

```python
"""Notion blocks <-> a documented markdown subset. Pure, no I/O.

Supported both ways: headings 1-3, paragraph, bulleted list, numbered list,
to-do, fenced code, quote, divider. Inline: bold, italic, inline code, links.
Anything else is a visible placeholder on read and a hard error on write.
"""

MAX_TEXT_LENGTH = 2000
INDENT = "  "

_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "paragraph": "",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
}


def plain_text(rich: list[dict] | None) -> str:
    """Concatenated text with annotations dropped. Used for titles."""
    return "".join(item.get("plain_text") or (item.get("text") or {}).get("content") or "" for item in rich or [])


def _annotated(rich: list[dict] | None) -> str:
    out = []
    for item in rich or []:
        content = (item.get("text") or {}).get("content")
        if content is None:
            content = item.get("plain_text") or ""
        annotations = item.get("annotations") or {}
        if annotations.get("code"):
            content = f"`{content}`"
        if annotations.get("bold"):
            content = f"**{content}**"
        if annotations.get("italic"):
            content = f"*{content}*"
        link = (item.get("text") or {}).get("link") or {}
        url = link.get("url") or item.get("href")
        if url:
            content = f"[{content}]({url})"
        out.append(content)
    return "".join(out)


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
    """Render blocks as markdown. Nested children are indented two spaces per level."""
    indent = INDENT * depth
    lines = []
    for block in blocks or []:
        kind = block.get("type") or ""
        payload = block.get(kind) or {}

        if kind == "divider":
            rendered = "---"
        elif kind == "code":
            language = payload.get("language") or ""
            rendered = f"```{language}\n{plain_text(payload.get('rich_text'))}\n```"
        elif kind == "to_do":
            mark = "x" if payload.get("checked") else " "
            rendered = f"- [{mark}] {_annotated(payload.get('rich_text'))}"
        elif kind in _PREFIX:
            rendered = f"{_PREFIX[kind]}{_annotated(payload.get('rich_text'))}"
        else:
            # Visible, not silently dropped: the model can see something is there.
            rendered = f"<!-- unsupported: {kind} -->"

        lines.append("\n".join(indent + line for line in rendered.split("\n")))

        children = block.get("children")
        if children:
            lines.append(blocks_to_markdown(children, depth + 1))

    return "\n\n".join(line for line in lines if line)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/markdown.py tests/test_markdown.py
git commit -m "feat: render Notion blocks as markdown"
```

---

### Task 4: Markdown to blocks

**Files:**
- Modify: `src/mcp_notion/markdown.py`
- Test: `tests/test_markdown.py`

**Interfaces:**
- Consumes: `MAX_TEXT_LENGTH`, `blocks_to_markdown` from Task 3
- Produces:
  - `class MarkdownError(Exception)` with `.message: str` and `.line: int`
  - `rich_text(content: str) -> list[dict]` — splits at `MAX_TEXT_LENGTH`
  - `markdown_to_blocks(markdown: str) -> list[dict]` — raises `MarkdownError`

- [ ] **Step 1: Write the failing tests (append to `tests/test_markdown.py`)**

```python
import pytest

from mcp_notion.markdown import MAX_TEXT_LENGTH, MarkdownError, markdown_to_blocks, rich_text


def test_rich_text_wraps_a_short_string():
    assert rich_text("hi") == [{"type": "text", "text": {"content": "hi"}}]


def test_rich_text_splits_at_the_two_thousand_character_limit():
    parts = rich_text("x" * (MAX_TEXT_LENGTH + 5))
    assert len(parts) == 2
    assert len(parts[0]["text"]["content"]) == MAX_TEXT_LENGTH
    assert len(parts[1]["text"]["content"]) == 5


def test_paragraph_becomes_a_paragraph_block():
    assert markdown_to_blocks("hello") == [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "hello"}}]}}
    ]


def test_headings_map_to_heading_blocks():
    blocks = markdown_to_blocks("# one\n## two\n### three")
    assert [b["type"] for b in blocks] == ["heading_1", "heading_2", "heading_3"]


def test_list_items_map_to_list_blocks():
    blocks = markdown_to_blocks("- a\n* b\n1. c")
    assert [b["type"] for b in blocks] == ["bulleted_list_item", "bulleted_list_item", "numbered_list_item"]


def test_to_do_checked_state_is_preserved():
    blocks = markdown_to_blocks("- [ ] open\n- [x] done")
    assert [b["type"] for b in blocks] == ["to_do", "to_do"]
    assert blocks[0]["to_do"]["checked"] is False
    assert blocks[1]["to_do"]["checked"] is True


def test_fenced_code_keeps_language_and_body():
    blocks = markdown_to_blocks("```python\nx = 1\ny = 2\n```")
    assert blocks[0]["code"]["language"] == "python"
    assert blocks[0]["code"]["rich_text"][0]["text"]["content"] == "x = 1\ny = 2"


def test_fence_without_a_language_defaults_to_plain_text():
    assert markdown_to_blocks("```\nx\n```")[0]["code"]["language"] == "plain text"


def test_quote_and_divider():
    blocks = markdown_to_blocks("> said\n\n---")
    assert [b["type"] for b in blocks] == ["quote", "divider"]


def test_blank_lines_do_not_produce_empty_blocks():
    assert len(markdown_to_blocks("a\n\n\n\nb")) == 2


def test_a_table_is_rejected_with_its_line_number():
    with pytest.raises(MarkdownError) as caught:
        markdown_to_blocks("fine\n\n| a | b |\n| - | - |")
    assert caught.value.line == 3
    assert "table" in caught.value.message.lower()


def test_an_image_is_rejected():
    with pytest.raises(MarkdownError) as caught:
        markdown_to_blocks("![alt](https://example.com/x.png)")
    assert "image" in caught.value.message.lower()


def test_raw_html_is_rejected():
    with pytest.raises(MarkdownError) as caught:
        markdown_to_blocks("<div>nope</div>")
    assert "html" in caught.value.message.lower()


def test_an_unterminated_fence_is_rejected():
    with pytest.raises(MarkdownError):
        markdown_to_blocks("```python\nx = 1")


def test_round_trip_preserves_every_supported_construct():
    source = "\n\n".join(
        [
            "# Heading",
            "## Sub",
            "### Deep",
            "a paragraph",
            "- bullet",
            "1. number",
            "- [ ] todo",
            "- [x] done",
            "> quoted",
            "```python\nx = 1\n```",
            "---",
        ]
    )
    assert blocks_to_markdown(markdown_to_blocks(source)) == source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: `ImportError: cannot import name 'MarkdownError'`.

- [ ] **Step 3: Append the write half to `src/mcp_notion/markdown.py`**

```python
import re

_UNSUPPORTED = (
    (re.compile(r"^\s*\|.*\|\s*$"), "table"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "image"),
    (re.compile(r"^\s*<[a-zA-Z/][^>]*>"), "raw HTML"),
)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.*)$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")
_TODO = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_DIVIDER = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")


class MarkdownError(Exception):
    """Raised when markdown contains a construct outside the supported subset.

    Caught at the tool boundary in server.py; never crosses the MCP boundary.
    The whole call fails: a partial write would leave a half-rendered page.
    """

    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.message = message
        self.line = line


def rich_text(content: str) -> list[dict]:
    """Wrap text as rich_text objects, split at Notion's 2000-character limit."""
    content = content or ""
    if not content:
        return []
    return [
        {"type": "text", "text": {"content": content[start : start + MAX_TEXT_LENGTH]}}
        for start in range(0, len(content), MAX_TEXT_LENGTH)
    ]


def _block(kind: str, **payload) -> dict:
    return {"object": "block", "type": kind, kind: payload}


def markdown_to_blocks(markdown: str) -> list[dict]:
    """Convert markdown to Notion blocks. Raises MarkdownError on anything unsupported.

    Inline emphasis is deliberately passed through as literal characters rather
    than parsed into annotations: a half-correct inline parser silently corrupts
    text, and Notion renders markdown-looking text acceptably as-is.
    """
    lines = (markdown or "").split("\n")
    blocks: list[dict] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        number = index + 1
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            language = fence.group(1) or "plain text"
            body: list[str] = []
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise MarkdownError("Unterminated code fence: no closing ```.", number)
            index += 1
            blocks.append(_block("code", rich_text=rich_text("\n".join(body)), language=language))
            continue

        for pattern, name in _UNSUPPORTED:
            if pattern.search(line):
                raise MarkdownError(
                    f"Unsupported markdown: {name}. Supported constructs are headings 1-3, "
                    "paragraphs, bulleted and numbered lists, to-dos, fenced code, quotes, "
                    "and dividers.",
                    number,
                )

        if _DIVIDER.match(line):
            blocks.append(_block("divider"))
        elif (match := _HEADING.match(line)) :
            blocks.append(_block(f"heading_{len(match.group(1))}", rich_text=rich_text(match.group(2))))
        elif (match := _TODO.match(line)) :
            blocks.append(_block("to_do", rich_text=rich_text(match.group(2)), checked=match.group(1).lower() == "x"))
        elif (match := _BULLET.match(line)) :
            blocks.append(_block("bulleted_list_item", rich_text=rich_text(match.group(1))))
        elif (match := _NUMBERED.match(line)) :
            blocks.append(_block("numbered_list_item", rich_text=rich_text(match.group(1))))
        elif (match := _QUOTE.match(line)) :
            blocks.append(_block("quote", rich_text=rich_text(match.group(1))))
        else:
            blocks.append(_block("paragraph", rich_text=rich_text(stripped)))
        index += 1

    return blocks
```

Note the ordering constraints, which the tests pin: the fence check precedes the unsupported scan, so `|` inside a code block is not mistaken for a table; `_DIVIDER` precedes `_BULLET`, so `---` is not read as a bullet; `_TODO` precedes `_BULLET`, and `_BULLET` carries a negative lookahead for `[ ]`, so a checkbox is never a plain bullet.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: all pass, round-trip included.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/markdown.py tests/test_markdown.py
git commit -m "feat: convert markdown to Notion blocks with explicit rejection"
```

---

### Task 5: Reading property values

**Files:**
- Create: `src/mcp_notion/properties.py`
- Test: `tests/test_properties.py`

**Interfaces:**
- Consumes: `plain_text` from `markdown.py` (Task 3)
- Produces:
  - `read_value(prop: dict) -> object`
  - `read_properties(props: dict) -> dict` — property name → plain value
  - `title_of(props: dict) -> str` — the value of the sole `title` property, or `""`

- [ ] **Step 1: Write the failing tests**

`tests/test_properties.py`:

```python
from mcp_notion.properties import read_properties, read_value, title_of


def rich(content):
    return [{"type": "text", "text": {"content": content}, "plain_text": content}]


def test_title_and_rich_text_flatten_to_strings():
    assert read_value({"type": "title", "title": rich("Spec")}) == "Spec"
    assert read_value({"type": "rich_text", "rich_text": rich("notes")}) == "notes"


def test_number_checkbox_and_scalars_pass_through():
    assert read_value({"type": "number", "number": 3}) == 3
    assert read_value({"type": "checkbox", "checkbox": True}) is True
    assert read_value({"type": "url", "url": "https://example.com"}) == "https://example.com"
    assert read_value({"type": "email", "email": "a@b.c"}) == "a@b.c"
    assert read_value({"type": "phone_number", "phone_number": "+1"}) == "+1"


def test_select_and_status_flatten_to_their_name():
    assert read_value({"type": "select", "select": {"name": "Done"}}) == "Done"
    assert read_value({"type": "status", "status": {"name": "In progress"}}) == "In progress"


def test_empty_select_is_none():
    assert read_value({"type": "select", "select": None}) is None


def test_multi_select_and_people_flatten_to_name_lists():
    assert read_value({"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]}) == ["a", "b"]
    assert read_value({"type": "people", "people": [{"name": "Ada"}]}) == ["Ada"]


def test_date_returns_start_alone_or_a_range():
    assert read_value({"type": "date", "date": {"start": "2026-09-07", "end": None}}) == "2026-09-07"
    assert read_value({"type": "date", "date": {"start": "2026-09-07", "end": "2026-09-09"}}) == {
        "start": "2026-09-07",
        "end": "2026-09-09",
    }
    assert read_value({"type": "date", "date": None}) is None


def test_relation_reports_a_count_not_uuids():
    assert read_value({"type": "relation", "relation": [{"id": "x"}, {"id": "y"}]}) == "2 related items"


def test_formula_and_rollup_unwrap_to_their_inner_value():
    assert read_value({"type": "formula", "formula": {"type": "string", "string": "abc"}}) == "abc"
    assert read_value({"type": "rollup", "rollup": {"type": "number", "number": 7}}) == 7


def test_timestamps_pass_through():
    assert read_value({"type": "created_time", "created_time": "2026-09-07T00:00:00.000Z"}) == "2026-09-07T00:00:00.000Z"


def test_an_unknown_type_is_labelled_not_crashed():
    assert read_value({"type": "wildcard", "wildcard": {"deep": 1}}) == "<unsupported property type: wildcard>"


def test_read_properties_maps_names_to_values():
    props = {"Name": {"type": "title", "title": rich("Spec")}, "Done": {"type": "checkbox", "checkbox": False}}
    assert read_properties(props) == {"Name": "Spec", "Done": False}


def test_title_of_finds_the_title_property_under_any_name():
    props = {"Task": {"type": "title", "title": rich("Ship it")}, "Done": {"type": "checkbox", "checkbox": False}}
    assert title_of(props) == "Ship it"


def test_title_of_returns_empty_string_when_absent():
    assert title_of({"Done": {"type": "checkbox", "checkbox": False}}) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_properties.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_notion.properties'`.

- [ ] **Step 3: Write the read half of `src/mcp_notion/properties.py`**

```python
"""Notion typed property values <-> plain Python. Pure, no I/O.

Reading flattens: a model does not benefit from Notion's nesting. Writing
consults the database schema, because the correct payload shape depends
entirely on the property's declared type.
"""

from .markdown import plain_text, rich_text

_SCALAR = ("number", "checkbox", "url", "email", "phone_number", "created_time", "last_edited_time")


def read_value(prop: dict) -> object:
    """One Notion property value as a plain Python value."""
    kind = (prop or {}).get("type") or ""
    value = (prop or {}).get(kind)

    if kind in ("title", "rich_text"):
        return plain_text(value)
    if kind in _SCALAR:
        return value
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind in ("multi_select", "people"):
        return [item.get("name") for item in value or []]
    if kind == "date":
        if not value:
            return None
        return {"start": value.get("start"), "end": value.get("end")} if value.get("end") else value.get("start")
    if kind == "relation":
        # Relations are lists of UUIDs, which no tool accepts back. A count is
        # the honest, useful summary; the page itself carries the detail.
        return f"{len(value or [])} related items"
    if kind in ("formula", "rollup"):
        inner = (value or {}).get("type") or ""
        return (value or {}).get(inner)
    return f"<unsupported property type: {kind}>"


def read_properties(props: dict) -> dict:
    return {name: read_value(prop) for name, prop in (props or {}).items()}


def title_of(props: dict) -> str:
    for prop in (props or {}).values():
        if (prop or {}).get("type") == "title":
            return plain_text(prop.get("title"))
    return ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_properties.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/properties.py tests/test_properties.py
git commit -m "feat: flatten Notion property values to plain Python"
```

---

### Task 6: Writing properties, filters, and sorts

**Files:**
- Modify: `src/mcp_notion/properties.py`
- Test: `tests/test_properties.py`

**Interfaces:**
- Consumes: `rich_text` from `markdown.py` (Task 4), `read_value` from Task 5
- Produces:
  - `class PropertyError(Exception)` with `.message: str`
  - `WRITABLE: frozenset[str]`
  - `write_value(value, kind: str, name: str) -> dict`
  - `write_properties(values: dict, schema: dict) -> dict` — `schema` maps name → `{"type": str, ...}`
  - `build_filter(filters: dict | None, schema: dict) -> dict | None`
  - `build_sorts(sort: dict | None, schema: dict) -> list[dict]`

- [ ] **Step 1: Write the failing tests (append to `tests/test_properties.py`)**

```python
import pytest

from mcp_notion.properties import PropertyError, build_filter, build_sorts, write_properties, write_value

SCHEMA = {
    "Name": {"type": "title"},
    "Notes": {"type": "rich_text"},
    "Points": {"type": "number"},
    "Stage": {"type": "select"},
    "State": {"type": "status"},
    "Tags": {"type": "multi_select"},
    "Done": {"type": "checkbox"},
    "Due": {"type": "date"},
    "Link": {"type": "url"},
    "Owner": {"type": "people"},
}


def test_write_value_builds_a_title_payload():
    assert write_value("Spec", "title", "Name") == {"title": [{"type": "text", "text": {"content": "Spec"}}]}


def test_write_value_builds_select_and_status_payloads():
    assert write_value("Done", "select", "Stage") == {"select": {"name": "Done"}}
    assert write_value("Todo", "status", "State") == {"status": {"name": "Todo"}}


def test_write_value_accepts_a_string_or_a_list_for_multi_select():
    assert write_value(["a", "b"], "multi_select", "Tags") == {"multi_select": [{"name": "a"}, {"name": "b"}]}
    assert write_value("a", "multi_select", "Tags") == {"multi_select": [{"name": "a"}]}


def test_write_value_builds_date_number_checkbox_and_url_payloads():
    assert write_value("2026-09-07", "date", "Due") == {"date": {"start": "2026-09-07"}}
    assert write_value({"start": "2026-09-07", "end": "2026-09-09"}, "date", "Due") == {
        "date": {"start": "2026-09-07", "end": "2026-09-09"}
    }
    assert write_value(3, "number", "Points") == {"number": 3}
    assert write_value(True, "checkbox", "Done") == {"checkbox": True}
    assert write_value("https://x.y", "url", "Link") == {"url": "https://x.y"}


def test_write_value_clears_a_property_when_given_none():
    assert write_value(None, "select", "Stage") == {"select": None}
    assert write_value(None, "rich_text", "Notes") == {"rich_text": []}


def test_write_value_rejects_a_non_writable_type_by_name():
    with pytest.raises(PropertyError) as caught:
        write_value("Ada", "people", "Owner")
    assert "Owner" in caught.value.message and "people" in caught.value.message


def test_write_properties_builds_a_payload_for_every_named_property():
    assert write_properties({"Name": "Spec", "Done": True}, SCHEMA) == {
        "Name": {"title": [{"type": "text", "text": {"content": "Spec"}}]},
        "Done": {"checkbox": True},
    }


def test_write_properties_rejects_an_unknown_name_and_lists_valid_ones():
    with pytest.raises(PropertyError) as caught:
        write_properties({"Nmae": "typo"}, SCHEMA)
    assert "Nmae" in caught.value.message
    assert "Name" in caught.value.message


def test_build_filter_returns_none_for_no_filters():
    assert build_filter(None, SCHEMA) is None
    assert build_filter({}, SCHEMA) is None


def test_build_filter_emits_a_bare_clause_for_one_property():
    assert build_filter({"Stage": "Done"}, SCHEMA) == {"property": "Stage", "select": {"equals": "Done"}}


def test_build_filter_ands_multiple_clauses():
    result = build_filter({"Stage": "Done", "Done": True}, SCHEMA)
    assert result == {
        "and": [
            {"property": "Stage", "select": {"equals": "Done"}},
            {"property": "Done", "checkbox": {"equals": True}},
        ]
    }


def test_build_filter_uses_contains_for_multi_select_and_rich_text():
    assert build_filter({"Tags": "urgent"}, SCHEMA) == {"property": "Tags", "multi_select": {"contains": "urgent"}}
    assert build_filter({"Notes": "abc"}, SCHEMA) == {"property": "Notes", "rich_text": {"contains": "abc"}}


def test_build_filter_rejects_an_unknown_property():
    with pytest.raises(PropertyError) as caught:
        build_filter({"Missing": 1}, SCHEMA)
    assert "Missing" in caught.value.message


def test_build_sorts_maps_a_property_and_direction():
    assert build_sorts({"property": "Points", "direction": "desc"}, SCHEMA) == [
        {"property": "Points", "direction": "descending"}
    ]


def test_build_sorts_defaults_to_ascending_and_tolerates_none():
    assert build_sorts({"property": "Points"}, SCHEMA) == [{"property": "Points", "direction": "ascending"}]
    assert build_sorts(None, SCHEMA) == []


def test_build_sorts_rejects_an_unknown_property():
    with pytest.raises(PropertyError):
        build_sorts({"property": "Missing"}, SCHEMA)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_properties.py -v`
Expected: `ImportError: cannot import name 'PropertyError'`.

- [ ] **Step 3: Append the write half to `src/mcp_notion/properties.py`**

```python
WRITABLE = frozenset(
    {"title", "rich_text", "number", "select", "status", "multi_select", "checkbox", "date", "url", "email", "phone_number"}
)

# Equality operator per type. Notion rejects the wrong operator outright, so
# this table is the whole of filter construction.
_FILTER_OPERATOR = {
    "title": "contains",
    "rich_text": "contains",
    "multi_select": "contains",
    "number": "equals",
    "select": "equals",
    "status": "equals",
    "checkbox": "equals",
    "date": "equals",
    "url": "equals",
    "email": "equals",
    "phone_number": "equals",
}


class PropertyError(Exception):
    """Raised when a property name or value cannot be written or filtered.

    Caught at the tool boundary in server.py; never crosses the MCP boundary.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def write_value(value, kind: str, name: str) -> dict:
    """Build the Notion payload for one property. `kind` comes from the schema."""
    if kind not in WRITABLE:
        raise PropertyError(
            f"Property '{name}' is of type '{kind}', which this server does not write. "
            f"Writable types: {', '.join(sorted(WRITABLE))}."
        )

    if kind == "title":
        return {"title": rich_text(value or "")}
    if kind == "rich_text":
        return {"rich_text": rich_text(value or "")}
    if kind in ("select", "status"):
        return {kind: {"name": value} if value is not None else None}
    if kind == "multi_select":
        names = value if isinstance(value, list) else ([value] if value is not None else [])
        return {"multi_select": [{"name": item} for item in names]}
    if kind == "date":
        if value is None:
            return {"date": None}
        if isinstance(value, dict):
            payload = {"start": value.get("start")}
            if value.get("end"):
                payload["end"] = value["end"]
            return {"date": payload}
        return {"date": {"start": value}}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    return {kind: value}


def write_properties(values: dict, schema: dict) -> dict:
    payload = {}
    for name, value in (values or {}).items():
        prop = (schema or {}).get(name)
        if prop is None:
            raise PropertyError(f"'{name}' is not a property of this database. Valid names: {_names(schema)}.")
        payload[name] = write_value(value, prop.get("type") or "", name)
    return payload


def build_filter(filters: dict | None, schema: dict) -> dict | None:
    """Property name -> value, AND-combined. Notion's raw filter syntax stays internal."""
    clauses = []
    for name, value in (filters or {}).items():
        prop = (schema or {}).get(name)
        if prop is None:
            raise PropertyError(f"'{name}' is not a property of this database. Valid names: {_names(schema)}.")
        kind = prop.get("type") or ""
        operator = _FILTER_OPERATOR.get(kind)
        if operator is None:
            raise PropertyError(
                f"Property '{name}' is of type '{kind}', which this server does not filter on. "
                f"Filterable types: {', '.join(sorted(_FILTER_OPERATOR))}."
            )
        clauses.append({"property": name, kind: {operator: value}})

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"and": clauses}


def build_sorts(sort: dict | None, schema: dict) -> list[dict]:
    if not sort:
        return []
    name = sort.get("property")
    if name not in (schema or {}):
        raise PropertyError(f"'{name}' is not a property of this database. Valid names: {_names(schema)}.")
    direction = "descending" if str(sort.get("direction", "asc")).lower().startswith("desc") else "ascending"
    return [{"property": name, "direction": direction}]


def _names(schema: dict) -> str:
    return ", ".join(sorted(schema or {}))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_properties.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/properties.py tests/test_properties.py
git commit -m "feat: build Notion property, filter, and sort payloads from the schema"
```

---

### Task 7: Node formatting

**Files:**
- Create: `src/mcp_notion/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: `plain_text` from `markdown.py` (Task 3), `read_properties`/`title_of` from `properties.py` (Task 5)
- Produces:
  - `page(node: dict) -> dict` — `{title, url, properties, created_time, last_edited_time}`
  - `database(node: dict) -> dict` — `{title, url}`
  - `schema(node: dict) -> dict` — property name → `{type, options?}`
  - `entity_title(node: dict) -> str` — title of a page **or** database node, for search results

- [ ] **Step 1: Write the failing tests**

`tests/test_format.py`:

```python
from mcp_notion import format as fmt

PAGE = {
    "object": "page",
    "id": "0123456789abcdef0123456789abcdef",
    "url": "https://www.notion.so/Spec-0123456789abcdef0123456789abcdef",
    "created_time": "2026-09-01T00:00:00.000Z",
    "last_edited_time": "2026-09-07T00:00:00.000Z",
    "properties": {
        "Name": {"type": "title", "title": [{"type": "text", "text": {"content": "Spec"}, "plain_text": "Spec"}]},
        "Done": {"type": "checkbox", "checkbox": True},
    },
}

DATABASE = {
    "object": "database",
    "id": "fedcba9876543210fedcba9876543210",
    "url": "https://www.notion.so/fedcba9876543210fedcba9876543210",
    "title": [{"type": "text", "text": {"content": "Tasks"}, "plain_text": "Tasks"}],
    "properties": {
        "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
        "Stage": {
            "id": "abc",
            "name": "Stage",
            "type": "select",
            "select": {"options": [{"name": "Todo"}, {"name": "Done"}]},
        },
    },
}


def test_page_exposes_title_url_and_flattened_properties():
    assert fmt.page(PAGE) == {
        "title": "Spec",
        "url": PAGE["url"],
        "properties": {"Name": "Spec", "Done": True},
        "created_time": "2026-09-01T00:00:00.000Z",
        "last_edited_time": "2026-09-07T00:00:00.000Z",
    }


def test_page_never_exposes_a_bare_uuid():
    assert "id" not in fmt.page(PAGE)


def test_database_exposes_title_and_url():
    assert fmt.database(DATABASE) == {"title": "Tasks", "url": DATABASE["url"]}


def test_schema_maps_names_to_types():
    assert fmt.schema(DATABASE)["Name"] == {"type": "title"}


def test_schema_includes_select_options():
    assert fmt.schema(DATABASE)["Stage"] == {"type": "select", "options": ["Todo", "Done"]}


def test_entity_title_handles_pages_and_databases():
    assert fmt.entity_title(PAGE) == "Spec"
    assert fmt.entity_title(DATABASE) == "Tasks"


def test_formatters_tolerate_missing_keys():
    assert fmt.page({}) == {"title": "", "url": None, "properties": {}, "created_time": None, "last_edited_time": None}
    assert fmt.database({}) == {"title": "", "url": None}
    assert fmt.schema({}) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_format.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_notion.format'`.

- [ ] **Step 3: Write `src/mcp_notion/format.py`**

```python
"""Notion nodes -> flat dicts. Pure functions, no I/O.

Bare UUIDs are deliberately dropped. Every entity carries its `url`, which is
itself a valid `ref` for a later call, so the id stays internal without
costing the model any addressability.
"""

from .markdown import plain_text
from .properties import read_properties, title_of

_OPTION_TYPES = ("select", "multi_select", "status")


def page(node: dict) -> dict:
    props = (node or {}).get("properties") or {}
    return {
        "title": title_of(props),
        "url": (node or {}).get("url"),
        "properties": read_properties(props),
        "created_time": (node or {}).get("created_time"),
        "last_edited_time": (node or {}).get("last_edited_time"),
    }


def database(node: dict) -> dict:
    return {"title": plain_text((node or {}).get("title")), "url": (node or {}).get("url")}


def schema(node: dict) -> dict:
    """Property name -> {type, options?}. Options are what the model may write."""
    out = {}
    for name, prop in ((node or {}).get("properties") or {}).items():
        kind = (prop or {}).get("type") or ""
        entry = {"type": kind}
        if kind in _OPTION_TYPES:
            options = ((prop or {}).get(kind) or {}).get("options") or []
            entry["options"] = [option.get("name") for option in options]
        out[name] = entry
    return out


def entity_title(node: dict) -> str:
    """Search returns pages and databases interleaved; each stores its title differently."""
    if (node or {}).get("object") == "database":
        return plain_text(node.get("title"))
    return title_of((node or {}).get("properties") or {})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_format.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/format.py tests/test_format.py
git commit -m "feat: flatten Notion page, database, and schema nodes"
```

---

### Task 8: Ref parsing

**Files:**
- Create: `src/mcp_notion/resolve.py`
- Test: `tests/test_resolve.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class ResolutionError(Exception)` with `.message: str` and `.candidates: list[dict]`
  - `aliases() -> dict[str, str]` — lowercased alias name → configured ref
  - `extract_id(ref: str) -> str | None` — dashed UUID, or `None` when the ref is not an id or URL

- [ ] **Step 1: Write the failing tests**

`tests/test_resolve.py`:

```python
import pytest

from mcp_notion.resolve import ResolutionError, aliases, extract_id

RAW = "0123456789abcdef0123456789abcdef"
DASHED = "01234567-89ab-cdef-0123-456789abcdef"


def test_extract_id_dashes_a_bare_uuid():
    assert extract_id(RAW) == DASHED


def test_extract_id_accepts_an_already_dashed_uuid():
    assert extract_id(DASHED) == DASHED


def test_extract_id_pulls_the_id_from_a_titled_page_url():
    assert extract_id(f"https://www.notion.so/myteam/Spec-Page-{RAW}") == DASHED


def test_extract_id_pulls_the_id_from_a_bare_url():
    assert extract_id(f"https://notion.so/{RAW}") == DASHED


def test_extract_id_ignores_a_view_query_string():
    assert extract_id(f"https://www.notion.so/myteam/{RAW}?v=ffffffffffffffffffffffffffffffff") == DASHED


def test_extract_id_returns_none_for_a_title():
    assert extract_id("Engineering Specs") is None


def test_extract_id_returns_none_for_empty_input():
    assert extract_id("") is None
    assert extract_id(None) is None


def test_aliases_reads_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("NOTION_ALIAS_TASKS", RAW)
    monkeypatch.setenv("NOTION_ALIAS_MY_SPECS", "https://notion.so/x")
    monkeypatch.setenv("NOTION_API_KEY", "ntn_test")
    result = aliases()
    assert result["tasks"] == RAW
    assert result["my_specs"] == "https://notion.so/x"
    assert "api_key" not in result


def test_resolution_error_carries_candidates():
    error = ResolutionError("ambiguous", [{"title": "a", "url": "u"}])
    assert error.message == "ambiguous"
    assert error.candidates == [{"title": "a", "url": "u"}]


def test_resolution_error_defaults_to_no_candidates():
    assert ResolutionError("nope").candidates == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'mcp_notion.resolve'`.

- [ ] **Step 3: Write the parsing half of `src/mcp_notion/resolve.py`**

```python
"""A caller's `ref` -> a Notion UUID, with an in-process cache.

A ref is tried as an alias, then a URL, then a bare UUID, then an exact title.
Ambiguity is always an error carrying candidates; the server never guesses
which page the caller meant.
"""

import os
import re

_ALIAS_PREFIX = "NOTION_ALIAS_"
_UUID = re.compile(r"([0-9a-fA-F]{8})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{12})")


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/resolve.py tests/test_resolve.py
git commit -m "feat: parse Notion refs from aliases, URLs, and ids"
```

---

### Task 9: Title resolution

**Files:**
- Modify: `src/mcp_notion/resolve.py`
- Test: `tests/test_resolve.py`

**Interfaces:**
- Consumes: `NotionClient.paginate` (Task 2), `entity_title` from `format.py` (Task 7), `aliases`/`extract_id`/`ResolutionError` (Task 8)
- Produces:
  - `Resolver(client: NotionClient)`
  - `Resolver.database(ref: str) -> str` — UUID
  - `Resolver.page(ref: str) -> str` — UUID
  - `Resolver.any(ref: str) -> tuple[str, str]` — `("database" | "page", uuid)`, for `create_page`'s parent
  - `Resolver.search(query: str, kind: str | None) -> list[dict]` — raw nodes

- [ ] **Step 1: Write the failing tests (append to `tests/test_resolve.py`)**

```python
import httpx
import respx

from mcp_notion.client import NOTION_API_URL, NotionClient
from mcp_notion.resolve import Resolver

OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_DASHED = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def page_node(title, page_id, url):
    return {
        "object": "page",
        "id": page_id,
        "url": url,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title, "text": {"content": title}}]}},
    }


def search_response(nodes):
    return httpx.Response(200, json={"results": nodes, "has_more": False, "next_cursor": None})


@pytest.fixture
def resolver():
    return Resolver(NotionClient(api_key="ntn_test"))


def test_alias_resolves_without_a_search(monkeypatch, resolver):
    monkeypatch.setenv("NOTION_ALIAS_TASKS", RAW)
    with respx.mock:
        route = respx.post(f"{NOTION_API_URL}/search")
        assert resolver.database("tasks") == DASHED
        assert route.call_count == 0


def test_alias_match_is_case_insensitive(monkeypatch, resolver):
    monkeypatch.setenv("NOTION_ALIAS_TASKS", RAW)
    assert resolver.database("TASKS") == DASHED


def test_a_url_resolves_without_a_search(resolver):
    with respx.mock:
        route = respx.post(f"{NOTION_API_URL}/search")
        assert resolver.page(f"https://www.notion.so/Spec-{RAW}") == DASHED
        assert route.call_count == 0


@respx.mock
def test_a_title_resolves_through_search(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response([page_node("Spec", RAW, "https://notion.so/a")])
    )
    assert resolver.page("Spec") == DASHED


@respx.mock
def test_title_matching_is_case_insensitive_but_exact(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response(
            [page_node("Spec Notes", OTHER, "https://notion.so/b"), page_node("spec", RAW, "https://notion.so/a")]
        )
    )
    assert resolver.page("Spec") == DASHED


@respx.mock
def test_two_exact_matches_raise_with_candidates(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response(
            [page_node("Spec", RAW, "https://notion.so/a"), page_node("Spec", OTHER, "https://notion.so/b")]
        )
    )
    with pytest.raises(ResolutionError) as caught:
        resolver.page("Spec")
    assert len(caught.value.candidates) == 2
    assert {c["url"] for c in caught.value.candidates} == {"https://notion.so/a", "https://notion.so/b"}


@respx.mock
def test_no_exact_match_raises_with_partial_candidates(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response([page_node("Spec Notes", OTHER, "https://notion.so/b")])
    )
    with pytest.raises(ResolutionError) as caught:
        resolver.page("Spec")
    assert caught.value.candidates[0]["title"] == "Spec Notes"


@respx.mock
def test_no_results_raises_with_a_sharing_hint(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=search_response([]))
    with pytest.raises(ResolutionError) as caught:
        resolver.page("Nothing")
    assert "share" in caught.value.message.lower()


@respx.mock
def test_a_client_error_during_search_raises_resolution_error(resolver):
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=httpx.Response(401, json={"object": "error", "message": "bad token"})
    )
    with pytest.raises(ResolutionError):
        resolver.page("Spec")


@respx.mock
def test_search_filters_by_object_kind(resolver):
    route = respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response([page_node("Spec", RAW, "https://notion.so/a")])
    )
    resolver.database("Spec")
    assert json.loads(route.calls.last.request.content)["filter"] == {"property": "object", "value": "database"}


@respx.mock
def test_a_resolved_title_is_cached(resolver):
    route = respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=search_response([page_node("Spec", RAW, "https://notion.so/a")])
    )
    resolver.page("Spec")
    resolver.page("Spec")
    assert route.call_count == 1


@respx.mock
def test_any_prefers_a_database_when_the_id_is_one(resolver):
    respx.get(f"{NOTION_API_URL}/databases/{DASHED}").mock(return_value=httpx.Response(200, json={"id": DASHED}))
    assert resolver.any(f"https://notion.so/{RAW}") == ("database", DASHED)


@respx.mock
def test_any_falls_back_to_page_when_the_id_is_not_a_database(resolver):
    respx.get(f"{NOTION_API_URL}/databases/{DASHED}").mock(
        return_value=httpx.Response(404, json={"object": "error", "message": "not a database"})
    )
    assert resolver.any(f"https://notion.so/{RAW}") == ("page", DASHED)
```

Add `import json` and `import pytest` to the top of the file if Step 1 of Task 8 did not already.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: `ImportError: cannot import name 'Resolver'`.

- [ ] **Step 3: Append the `Resolver` to `src/mcp_notion/resolve.py`**

```python
from . import format as fmt
from .client import NotionClient

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
            return ("database", resolved) if "error" not in body else ("page", resolved)
        try:
            return ("database", self._resolve(ref, "database"))
        except ResolutionError:
            return ("page", self._resolve(ref, "page"))

    def search(self, query: str, kind: str | None = None) -> list[dict]:
        """Raw search nodes. Raises ResolutionError if the request itself fails."""
        payload: dict = {"query": query or ""}
        if kind:
            payload["filter"] = {"property": "object", "value": kind}
        body = self._client.paginate("POST", "/search", payload)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/resolve.py tests/test_resolve.py
git commit -m "feat: resolve Notion refs by title with explicit ambiguity errors"
```

---

### Task 10: Read tools

**Files:**
- Modify: `src/mcp_notion/server.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: everything produced by Tasks 2 and 5-9
- Produces:
  - `list_databases() -> list[dict]`
  - `get_database_schema(ref: str) -> dict`
  - `query_database(ref: str, filter: dict | None = None, sort: dict | None = None, limit: int = 25) -> list[dict]`
  - `get_page(ref: str) -> dict`
  - `search(query: str, kind: str | None = None) -> list[dict]`
  - Module helpers used again in Task 11: `_get_client()`, `_get_resolver()`, `_fail(exc)`, `_page_url(page_id)`

- [ ] **Step 1: Write the failing tests**

`tests/test_tools.py`:

```python
import httpx
import pytest
import respx

from mcp_notion import server
from mcp_notion.client import NOTION_API_URL

RAW = "0123456789abcdef0123456789abcdef"
DASHED = "01234567-89ab-cdef-0123-456789abcdef"
DB_RAW = "fedcba9876543210fedcba9876543210"
DB_DASHED = "fedcba98-7654-3210-fedc-ba9876543210"
PAGE_URL = f"https://www.notion.so/Spec-{RAW}"
DB_URL = f"https://www.notion.so/Tasks-{DB_RAW}"


def title_prop(text):
    return {"type": "title", "title": [{"plain_text": text, "text": {"content": text}}]}


PAGE_NODE = {
    "object": "page",
    "id": DASHED,
    "url": PAGE_URL,
    "parent": {"type": "database_id", "database_id": DB_DASHED},
    "created_time": "2026-09-01T00:00:00.000Z",
    "last_edited_time": "2026-09-07T00:00:00.000Z",
    "properties": {"Name": title_prop("Spec"), "Done": {"type": "checkbox", "checkbox": False}},
}

DB_NODE = {
    "object": "database",
    "id": DB_DASHED,
    "url": DB_URL,
    "title": [{"plain_text": "Tasks", "text": {"content": "Tasks"}}],
    "properties": {
        "Name": {"name": "Name", "type": "title", "title": {}},
        "Done": {"name": "Done", "type": "checkbox", "checkbox": {}},
        "Stage": {"name": "Stage", "type": "select", "select": {"options": [{"name": "Todo"}]}},
    },
}


def listing(nodes):
    return httpx.Response(200, json={"results": nodes, "has_more": False, "next_cursor": None})


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    """Each test gets a fresh client and resolver, and a known environment."""
    for name in list(server.os.environ):
        if name.startswith("NOTION_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NOTION_API_KEY", "ntn_test")
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setattr(server, "_resolver", None)


def test_a_missing_api_key_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.setattr(server, "_client", None)
    assert "NOTION_API_KEY" in server.get_page(PAGE_URL)["error"]


@respx.mock
def test_list_databases_returns_titles_and_urls():
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([DB_NODE]))
    assert server.list_databases() == [{"title": "Tasks", "url": DB_URL, "alias": None}]


@respx.mock
def test_list_databases_attaches_a_configured_alias(monkeypatch):
    monkeypatch.setenv("NOTION_ALIAS_TASKS", DB_URL)
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([DB_NODE]))
    assert server.list_databases()[0]["alias"] == "tasks"


@respx.mock
def test_list_databases_reports_an_alias_that_matched_nothing(monkeypatch):
    monkeypatch.setenv("NOTION_ALIAS_GHOST", "https://notion.so/" + "b" * 32)
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([]))
    entry = server.list_databases()[0]
    assert entry["alias"] == "ghost" and entry["title"] is None


@respx.mock
def test_list_databases_reports_a_transport_error_as_a_single_element_list():
    respx.post(f"{NOTION_API_URL}/search").mock(
        return_value=httpx.Response(401, json={"object": "error", "message": "bad token"})
    )
    result = server.list_databases()
    assert len(result) == 1 and "error" in result[0]


@respx.mock
def test_get_database_schema_returns_types_and_options():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    result = server.get_database_schema(DB_URL)
    assert result["title"] == "Tasks"
    assert result["properties"]["Stage"] == {"type": "select", "options": ["Todo"]}


@respx.mock
def test_get_database_schema_reports_an_unresolvable_ref():
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([]))
    assert "error" in server.get_database_schema("Nowhere")


@respx.mock
def test_query_database_returns_flat_rows():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    respx.post(f"{NOTION_API_URL}/databases/{DB_DASHED}/query").mock(return_value=listing([PAGE_NODE]))
    rows = server.query_database(DB_URL)
    assert rows == [
        {
            "title": "Spec",
            "url": PAGE_URL,
            "properties": {"Name": "Spec", "Done": False},
            "created_time": "2026-09-01T00:00:00.000Z",
            "last_edited_time": "2026-09-07T00:00:00.000Z",
        }
    ]


@respx.mock
def test_query_database_sends_a_translated_filter_and_sort():
    import json

    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.post(f"{NOTION_API_URL}/databases/{DB_DASHED}/query").mock(return_value=listing([]))
    server.query_database(DB_URL, filter={"Stage": "Todo"}, sort={"property": "Name", "direction": "desc"})
    body = json.loads(route.calls.last.request.content)
    assert body["filter"] == {"property": "Stage", "select": {"equals": "Todo"}}
    assert body["sorts"] == [{"property": "Name", "direction": "descending"}]


@respx.mock
def test_query_database_honours_limit():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    respx.post(f"{NOTION_API_URL}/databases/{DB_DASHED}/query").mock(return_value=listing([PAGE_NODE, PAGE_NODE, PAGE_NODE]))
    assert len(server.query_database(DB_URL, limit=2)) == 2


@respx.mock
def test_query_database_reports_an_unknown_filter_property():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    result = server.query_database(DB_URL, filter={"Nope": 1})
    assert len(result) == 1 and "Nope" in result[0]["error"]


@respx.mock
def test_get_page_returns_properties_and_markdown():
    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    respx.get(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        return_value=listing(
            [
                {
                    "id": "b1",
                    "type": "heading_1",
                    "has_children": False,
                    "heading_1": {"rich_text": [{"plain_text": "Title", "text": {"content": "Title"}}]},
                }
            ]
        )
    )
    result = server.get_page(PAGE_URL)
    assert result["title"] == "Spec"
    assert result["markdown"] == "# Title"


@respx.mock
def test_get_page_reports_a_sharing_failure():
    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(
        return_value=httpx.Response(404, json={"object": "error", "message": "Could not find page."})
    )
    assert "share" in server.get_page(PAGE_URL)["error"].lower()


@respx.mock
def test_search_returns_titles_urls_and_kinds():
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([PAGE_NODE, DB_NODE]))
    assert server.search("spec") == [
        {"title": "Spec", "url": PAGE_URL, "kind": "page"},
        {"title": "Tasks", "url": DB_URL, "kind": "database"},
    ]


def test_search_rejects_an_unknown_kind():
    result = server.search("spec", kind="block")
    assert len(result) == 1 and "error" in result[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tools.py -v`
Expected: `AttributeError: module 'mcp_notion.server' has no attribute 'list_databases'`.

- [ ] **Step 3: Add the read tools to `src/mcp_notion/server.py`**

Insert after the `mcp = FastMCP(...)` block from Task 1:

```python
from . import format as fmt
from .client import NotionClient
from .markdown import MarkdownError, blocks_to_markdown, markdown_to_blocks, rich_text
from .properties import PropertyError, build_filter, build_sorts, write_properties, write_value
from .resolve import ResolutionError, Resolver, aliases, extract_id

DEFAULT_LIMIT = 25
MAX_CHILDREN_ON_CREATE = 100

_client: NotionClient | None = None
_resolver: Resolver | None = None


def _get_client() -> NotionClient:
    global _client
    if _client is None:
        key = os.environ.get("NOTION_API_KEY")
        if not key:
            raise RuntimeError("NOTION_API_KEY environment variable is not set")
        _client = NotionClient(api_key=key)
    return _client


def _get_resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        _resolver = Resolver(_get_client())
    return _resolver


def _fail(exc: Exception) -> dict:
    """Any expected failure as a plain error dict. Candidates ride along when present."""
    if isinstance(exc, MarkdownError):
        return {"error": f"Line {exc.line}: {exc.message}"}
    message = getattr(exc, "message", None) or str(exc)
    candidates = getattr(exc, "candidates", None)
    return {"error": message, "candidates": candidates} if candidates else {"error": message}


def _page_url(page_id: str) -> str:
    """A page id is a valid notion.so URL, and a valid ref for a later call."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _schema_of(database_id: str) -> dict:
    """The database node, or a dict with an "error" key. Callers need both halves."""
    return _get_client().request("GET", f"/databases/{database_id}")


@mcp.tool()
def list_databases() -> list[dict]:
    """
    List Notion databases visible to this integration, as {title, url, alias}.
    `alias` is the name configured via NOTION_ALIAS_*, or null. Aliases that
    match no visible database are listed with a null title — they are still
    usable as a `ref`, but the integration may not have access yet.
    """
    try:
        nodes = _get_resolver().search("", "database")
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]

    configured = {name: extract_id(value) for name, value in aliases().items()}
    by_id = {}
    listed = []
    for node in nodes:
        entry = fmt.database(node)
        entry["alias"] = None
        by_id[extract_id(node.get("id"))] = entry
        listed.append(entry)

    for name, database_id in configured.items():
        if database_id in by_id:
            by_id[database_id]["alias"] = name
        else:
            listed.append({"title": None, "url": _page_url(database_id or ""), "alias": name})
    return listed


@mcp.tool()
def get_database_schema(ref: str) -> dict:
    """
    Property names and types for a database, as {title, url, properties}.
    Call this before filtering with query_database or writing with update_row.
    ref: an alias, a notion.so URL, an id, or an exact database title.
    """
    try:
        database_id = _get_resolver().database(ref)
    except (ResolutionError, RuntimeError) as e:
        return _fail(e)

    node = _schema_of(database_id)
    if "error" in node:
        return node
    return {**fmt.database(node), "properties": fmt.schema(node)}


@mcp.tool()
def query_database(
    ref: str, filter: dict | None = None, sort: dict | None = None, limit: int = DEFAULT_LIMIT
) -> list[dict]:
    """
    Rows of a Notion database, as {title, url, properties}.
    ref: an alias, a notion.so URL, an id, or an exact database title.
    filter: property name -> value, combined with AND. Text properties match by
      substring, everything else by equality. Names must come from
      get_database_schema.
    sort: {"property": name, "direction": "asc" | "desc"}.
    limit: maximum rows to return.
    """
    try:
        database_id = _get_resolver().database(ref)
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]

    node = _schema_of(database_id)
    if "error" in node:
        return [node]

    schema = fmt.schema(node)
    try:
        payload: dict = {}
        clause = build_filter(filter, schema)
        if clause:
            payload["filter"] = clause
        sorts = build_sorts(sort, schema)
        if sorts:
            payload["sorts"] = sorts
    except PropertyError as e:
        return [_fail(e)]

    body = _get_client().paginate("POST", f"/databases/{database_id}/query", payload)
    if "error" in body:
        return [body]
    return [fmt.page(row) for row in body["results"][: max(limit, 0)]]


@mcp.tool()
def get_page(ref: str) -> dict:
    """
    A Notion page, as {title, url, properties, markdown}. The body is markdown;
    block types outside the supported subset appear as
    `<!-- unsupported: TYPE -->`.
    ref: an alias, a notion.so URL, an id, or an exact page title.
    """
    try:
        page_id = _get_resolver().page(ref)
    except (ResolutionError, RuntimeError) as e:
        return _fail(e)

    node = _get_client().request("GET", f"/pages/{page_id}")
    if "error" in node:
        return node

    blocks = _get_client().fetch_block_tree(page_id)
    if isinstance(blocks, dict):
        return blocks
    return {**fmt.page(node), "markdown": blocks_to_markdown(blocks)}


@mcp.tool()
def search(query: str, kind: str | None = None) -> list[dict]:
    """
    Search titles of pages and databases shared with this integration, as
    {title, url, kind}. Use it to find the URL to pass as a `ref`.
    kind: "page", "database", or omitted for both.
    """
    if kind not in (None, "page", "database"):
        return [{"error": f"kind must be 'page', 'database', or omitted, not '{kind}'."}]
    try:
        nodes = _get_resolver().search(query, kind)
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]
    return [
        {"title": fmt.entity_title(node), "url": node.get("url"), "kind": node.get("object")}
        for node in nodes[:DEFAULT_LIMIT]
    ]
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_notion/server.py tests/test_tools.py
git commit -m "feat: add Notion read tools"
```

---

### Task 11: Write tools, README, and final smoke test

**Files:**
- Modify: `src/mcp_notion/server.py`
- Modify: `tests/test_tools.py`, `tests/test_smoke.py`
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 2 and 5-10
- Produces:
  - `create_page(parent_ref: str, title: str, markdown: str | None = None, properties: dict | None = None) -> dict`
  - `append_to_page(ref: str, markdown: str) -> dict`
  - `update_row(ref: str, properties: dict) -> dict`

- [ ] **Step 1: Write the failing tests (append to `tests/test_tools.py`, and replace `tests/test_smoke.py`)**

Append to `tests/test_tools.py`:

```python
@respx.mock
def test_create_page_in_a_database_sets_the_title_property():
    import json

    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.post(f"{NOTION_API_URL}/pages").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    result = server.create_page(DB_URL, "Spec", markdown="hello", properties={"Done": True})
    body = json.loads(route.calls.last.request.content)
    assert body["parent"] == {"database_id": DB_DASHED}
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "Spec"
    assert body["properties"]["Done"] == {"checkbox": True}
    assert body["children"][0]["type"] == "paragraph"
    assert result["url"] == PAGE_URL


@respx.mock
def test_create_page_under_a_page_uses_the_literal_title_key():
    import json

    respx.get(f"{NOTION_API_URL}/databases/{DASHED}").mock(
        return_value=httpx.Response(404, json={"object": "error", "message": "not a database"})
    )
    route = respx.post(f"{NOTION_API_URL}/pages").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    server.create_page(PAGE_URL, "Child")
    body = json.loads(route.calls.last.request.content)
    assert body["parent"] == {"page_id": DASHED}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == "Child"


@respx.mock
def test_create_page_appends_children_beyond_the_first_hundred():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    respx.post(f"{NOTION_API_URL}/pages").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    append = respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    server.create_page(DB_URL, "Spec", markdown="\n\n".join(f"line {n}" for n in range(120)))
    assert append.call_count == 1


@respx.mock
def test_create_page_rejects_unsupported_markdown_before_any_write():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.post(f"{NOTION_API_URL}/pages")
    result = server.create_page(DB_URL, "Spec", markdown="| a | b |")
    assert "table" in result["error"].lower()
    assert route.call_count == 0


@respx.mock
def test_append_to_page_reports_the_block_count_and_url():
    route = respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    assert server.append_to_page(PAGE_URL, "# Update\n\nbody") == {
        "appended": 2,
        "url": f"https://www.notion.so/{RAW}",
    }
    assert route.call_count == 1


@respx.mock
def test_append_to_page_rejects_empty_markdown_without_calling_notion():
    route = respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children")
    assert "error" in server.append_to_page(PAGE_URL, "   ")
    assert route.call_count == 0


@respx.mock
def test_update_row_writes_translated_properties():
    import json

    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.patch(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    result = server.update_row(PAGE_URL, {"Done": True, "Stage": "Todo"})
    body = json.loads(route.calls.last.request.content)
    assert body["properties"] == {"Done": {"checkbox": True}, "Stage": {"select": {"name": "Todo"}}}
    assert result["title"] == "Spec"


@respx.mock
def test_update_row_rejects_a_page_that_is_not_a_database_row():
    node = {**PAGE_NODE, "parent": {"type": "workspace", "workspace": True}}
    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=node))
    assert "database" in server.update_row(PAGE_URL, {"Done": True})["error"].lower()


@respx.mock
def test_update_row_reports_an_unknown_property_with_valid_names():
    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    error = server.update_row(PAGE_URL, {"Dnoe": True})["error"]
    assert "Dnoe" in error and "Done" in error
```

Replace `tests/test_smoke.py` entirely:

```python
import asyncio

from mcp_notion import server

EXPECTED_TOOLS = {
    "list_databases",
    "get_database_schema",
    "query_database",
    "get_page",
    "search",
    "create_page",
    "append_to_page",
    "update_row",
}


def test_server_exposes_a_named_mcp_instance():
    assert server.mcp.name == "Notion"


def test_main_is_callable():
    assert callable(server.main)


def test_exactly_the_eight_expected_tools_are_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_every_tool_documents_itself():
    tools = asyncio.run(server.mcp.list_tools())
    assert all(tool.description and tool.description.strip() for tool in tools)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tools.py tests/test_smoke.py -v`
Expected: `AttributeError: module 'mcp_notion.server' has no attribute 'create_page'`, and the tool-set assertion failing with the three write tools missing.

- [ ] **Step 3: Append the write tools to `src/mcp_notion/server.py`**

```python
@mcp.tool()
def create_page(
    parent_ref: str, title: str, markdown: str | None = None, properties: dict | None = None
) -> dict:
    """
    Create a page, either as a row in a database or as a subpage of another page.
    parent_ref: an alias, a notion.so URL, an id, or an exact title.
    title: the new page's title.
    markdown: optional page body. Headings 1-3, paragraphs, bulleted and
      numbered lists, to-dos, fenced code, quotes, and dividers are supported;
      anything else fails the call before Notion is touched.
    properties: for a database parent only, property name -> value. Call
      get_database_schema first for the valid names.
    """
    try:
        kind, parent_id = _get_resolver().any(parent_ref)
        blocks = markdown_to_blocks(markdown) if markdown else []
    except (ResolutionError, MarkdownError, RuntimeError) as e:
        return _fail(e)

    if kind == "database":
        node = _schema_of(parent_id)
        if "error" in node:
            return node
        schema = fmt.schema(node)
        title_name = next((name for name, prop in schema.items() if prop["type"] == "title"), None)
        if title_name is None:
            return {"error": "That database has no title property, so a page cannot be created in it."}
        try:
            payload = write_properties(properties or {}, schema)
        except PropertyError as e:
            return _fail(e)
        payload[title_name] = write_value(title, "title", title_name)
        parent = {"database_id": parent_id}
    else:
        if properties:
            return {"error": "properties apply only to database rows. A subpage takes a title and a body."}
        # A page-parented page has one property, keyed by the literal "title".
        payload = {"title": {"title": rich_text(title)}}
        parent = {"page_id": parent_id}

    body: dict = {"parent": parent, "properties": payload}
    if blocks:
        body["children"] = blocks[:MAX_CHILDREN_ON_CREATE]

    created = _get_client().request("POST", "/pages", json=body)
    if "error" in created:
        return created

    overflow = blocks[MAX_CHILDREN_ON_CREATE:]
    if overflow:
        # POST /pages caps children at 100; the rest is a normal append.
        appended = _get_client().append_blocks(created["id"], overflow)
        if "error" in appended:
            return {**fmt.page(created), "error": f"Page created, but part of the body failed: {appended['error']}"}
    return fmt.page(created)


@mcp.tool()
def append_to_page(ref: str, markdown: str) -> dict:
    """
    Append markdown to the end of a page, as {appended, url}. Existing content is
    never modified or removed.
    ref: an alias, a notion.so URL, an id, or an exact page title.
    markdown: same supported subset as create_page.
    """
    try:
        page_id = _get_resolver().page(ref)
        blocks = markdown_to_blocks(markdown)
    except (ResolutionError, MarkdownError, RuntimeError) as e:
        return _fail(e)

    if not blocks:
        return {"error": "Nothing to append: the markdown is empty."}

    result = _get_client().append_blocks(page_id, blocks)
    if "error" in result:
        return result
    return {"appended": result["appended"], "url": _page_url(page_id)}


@mcp.tool()
def update_row(ref: str, properties: dict) -> dict:
    """
    Set properties on a database row, as {title, url, properties}. Only the named
    properties change; the page body is untouched.
    ref: an alias, a notion.so URL, an id, or an exact row title.
    properties: property name -> value. Call get_database_schema first for the
      valid names and, for select and status properties, the valid options.
    """
    if not properties:
        return {"error": "No properties given. Pass property name -> value."}
    try:
        page_id = _get_resolver().page(ref)
    except (ResolutionError, RuntimeError) as e:
        return _fail(e)

    node = _get_client().request("GET", f"/pages/{page_id}")
    if "error" in node:
        return node

    parent = node.get("parent") or {}
    database_id = parent.get("database_id")
    if not database_id:
        return {"error": "That page is not a database row, so it has no properties to set. Use append_to_page instead."}

    schema_node = _schema_of(database_id)
    if "error" in schema_node:
        return schema_node

    try:
        payload = write_properties(properties, fmt.schema(schema_node))
    except PropertyError as e:
        return _fail(e)

    updated = _get_client().request("PATCH", f"/pages/{page_id}", json={"properties": payload})
    if "error" in updated:
        return updated
    return fmt.page(updated)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: all pass, including the eight-tool smoke assertion.

- [ ] **Step 5: Write `README.md`**

```markdown
# mcp-notion

An MCP server exposing eight narrow Notion tools. Reads pages and databases,
writes additively. Nothing is deleted, archived, or overwritten.

## Setup

1. Create an internal integration at <https://www.notion.com/my-integrations>
   and copy its token.
2. `cp .env.example .env` and set `NOTION_API_KEY`.
3. **Share each page or database with the integration.** In Notion, open it,
   then ••• > Connections > your integration. Without this, every call returns
   a 404 — the token alone grants no access.
4. Install: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`

Optional aliases, so a database can be named rather than pasted:

```
NOTION_ALIAS_TASKS=https://www.notion.so/workspace/0123456789abcdef0123456789abcdef
```

`list_databases` reports configured aliases.

## Registering with Claude Code

```bash
claude mcp add notion -- /absolute/path/to/mcp-notion/.venv/bin/mcp-notion
```

## Refs

Every tool takes a `ref`, resolved in this order: configured alias, notion.so
URL, page or database id, exact title. An ambiguous title is an error listing
candidate URLs — the server never guesses.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_databases` | Databases visible to the integration, with aliases |
| `get_database_schema` | Property names, types, and select options |
| `query_database` | Rows, filtered and sorted by property name |
| `get_page` | Page properties plus the body as markdown |
| `search` | Title search across shared pages and databases |
| `create_page` | New database row or subpage, with an optional markdown body |
| `append_to_page` | Append markdown to the end of a page |
| `update_row` | Set properties on a database row |

## Markdown subset

Both directions: headings 1-3, paragraphs, bulleted and numbered lists, to-dos,
fenced code, quotes, dividers. Reading an unsupported block yields
`<!-- unsupported: TYPE -->`. Writing an unsupported construct — tables, images,
raw HTML — fails the whole call, naming the construct and the line.

## Tests

```bash
.venv/bin/pytest
```

No network, no API key required.
```

- [ ] **Step 6: Verify the whole suite and commit**

Run: `.venv/bin/pytest -q`
Expected: all pass, zero failures.

```bash
git add src/mcp_notion/server.py tests/test_tools.py tests/test_smoke.py README.md
git commit -m "feat: add Notion write tools and README"
```

---

## Definition of done

- `.venv/bin/pytest -q` passes with no `NOTION_API_KEY` set and no network.
- `tests/test_smoke.py` asserts exactly the eight expected tool names.
- No tool raises; every failure path returns `{"error": ...}` or `[{"error": ...}]`.
- No bare UUID appears in any tool's return value.
- No archive, delete, or body-replacement code exists anywhere in `src/`.
- `.env` is untracked; no real token appears in any committed file.
