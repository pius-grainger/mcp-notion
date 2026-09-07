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
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/{DASHED}/children.*").mock(
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
