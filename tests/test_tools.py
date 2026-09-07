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
def test_list_databases_reports_an_unparseable_alias_with_a_null_url(monkeypatch):
    monkeypatch.setenv("NOTION_ALIAS_MYSTERY", "Not a URL or id")
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([]))
    entry = server.list_databases()[0]
    assert entry["alias"] == "mystery" and entry["url"] is None


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
def test_query_database_stops_fetching_once_the_limit_is_satisfied():
    """The limit must bound the fetch, not just the answer. Three pages exist;
    asking for one row must cost one query, not three."""
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    query = respx.post(f"{NOTION_API_URL}/databases/{DB_DASHED}/query").mock(
        side_effect=[
            httpx.Response(200, json={"results": [PAGE_NODE], "has_more": True, "next_cursor": "a"}),
            httpx.Response(200, json={"results": [PAGE_NODE], "has_more": True, "next_cursor": "b"}),
            httpx.Response(200, json={"results": [PAGE_NODE], "has_more": False, "next_cursor": None}),
        ]
    )
    rows = server.query_database(DB_URL, limit=1)
    assert len(rows) == 1
    assert query.call_count == 1


@respx.mock
def test_query_database_returns_an_error_dict_when_something_unforeseen_raises(monkeypatch):
    """The no-exception-escapes invariant cannot rest on enumerating exception
    types; an unforeseen one used to escape query_database as a raised exception."""
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))

    def boom(node):
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(server.fmt, "schema", boom)
    result = server.query_database(DB_URL)
    assert len(result) == 1
    assert "AttributeError" in result[0]["error"]
    assert "query_database" in result[0]["error"]


@respx.mock
def test_get_page_returns_an_error_dict_when_something_unforeseen_raises(monkeypatch):
    """The dict-returning half of the same guarantee."""
    respx.get(f"{NOTION_API_URL}/pages/{DASHED}").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/{DASHED}/children.*").mock(return_value=listing([]))

    def boom(blocks):
        raise TypeError("unhashable type")

    monkeypatch.setattr(server, "blocks_to_markdown", boom)
    result = server.get_page(PAGE_URL)
    assert isinstance(result, dict)
    assert "TypeError" in result["error"]


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


@respx.mock
def test_search_caps_results_at_the_documented_limit():
    respx.post(f"{NOTION_API_URL}/search").mock(return_value=listing([PAGE_NODE] * 30))
    assert len(server.search("spec")) == server.DEFAULT_LIMIT


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
    import json

    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    create = respx.post(f"{NOTION_API_URL}/pages").mock(return_value=httpx.Response(200, json=PAGE_NODE))
    append = respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    server.create_page(DB_URL, "Spec", markdown="\n\n".join(f"line {n}" for n in range(120)))
    assert append.call_count == 1
    created_body = json.loads(create.calls.last.request.content)
    appended_body = json.loads(append.calls.last.request.content)
    assert len(created_body["children"]) == 100
    assert len(appended_body["children"]) == 20


@respx.mock
def test_create_page_shows_the_ambiguous_database_candidates_for_its_parent():
    """Two databases are titled "Tasks" and no page is. Reporting the page
    branch's failure discards both candidates and blames a sharing problem the
    user does not have."""

    def respond(request):
        import json as json_module

        kind = (json_module.loads(request.content).get("filter") or {}).get("value")
        return listing([DB_NODE, {**DB_NODE, "id": DASHED, "url": "https://notion.so/other"}] if kind == "database" else [])

    respx.post(f"{NOTION_API_URL}/search").mock(side_effect=respond)
    route = respx.post(f"{NOTION_API_URL}/pages")
    result = server.create_page("Tasks", "New row")
    assert [c["url"] for c in result["candidates"]] == [DB_URL, "https://notion.so/other"]
    assert "matches 2 databases" in result["error"]
    assert route.call_count == 0


@respx.mock
def test_create_page_rejects_unsupported_markdown_before_any_write():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.post(f"{NOTION_API_URL}/pages")
    result = server.create_page(DB_URL, "Spec", markdown="| a | b |")
    assert "table" in result["error"].lower()
    assert route.call_count == 0


@respx.mock
def test_create_page_rejects_a_title_passed_through_properties():
    respx.get(f"{NOTION_API_URL}/databases/{DB_DASHED}").mock(return_value=httpx.Response(200, json=DB_NODE))
    route = respx.post(f"{NOTION_API_URL}/pages")
    result = server.create_page(DB_URL, "Spec", properties={"Name": "Sneaky Title"})
    assert "error" in result
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
def test_append_to_page_reports_the_blocks_that_landed_before_a_failure():
    """250 blocks, rate limited on the second chunk: 100 blocks are already in
    Notion. Returning a bare error tells the caller the write failed, and the
    obvious retry then writes 350 blocks."""
    route = respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"}),
            httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"}),
        ]
    )
    result = server.append_to_page(PAGE_URL, "\n\n".join(f"line {n}" for n in range(250)))
    assert route.call_count == 3
    assert result["appended"] == 100
    assert "duplicate" in result["error"].lower()
    assert "rate limit" in result["error"].lower()


@respx.mock
def test_append_to_page_reports_a_first_chunk_failure_as_a_plain_error():
    respx.patch(f"{NOTION_API_URL}/blocks/{DASHED}/children").mock(
        return_value=httpx.Response(400, json={"object": "error", "message": "bad block"})
    )
    result = server.append_to_page(PAGE_URL, "hello")
    assert result["appended"] == 0
    assert "duplicate" not in result["error"].lower()


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
