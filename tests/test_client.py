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
def test_request_includes_the_http_status_on_an_error(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(
        return_value=httpx.Response(
            401, json={"object": "error", "status": 401, "code": "unauthorized", "message": "API token is invalid."}
        )
    )
    assert client.request("GET", "/pages/abc")["status"] == 401


@respx.mock
def test_request_omits_the_status_key_on_a_transport_failure(client):
    respx.get(f"{NOTION_API_URL}/pages/abc").mock(side_effect=httpx.ConnectError("boom"))
    assert "status" not in client.request("GET", "/pages/abc")


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
    """250 blocks is three chunks, and the second fails: the third must not be sent.

    With 150 blocks (two chunks, failing on the last) the assertion could not
    tell "stopped on the error" from "ran every chunk".
    """
    route = respx.patch(f"{NOTION_API_URL}/blocks/abc/children").mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(400, json={"object": "error", "message": "bad block"}),
            httpx.Response(200, json={"results": []}),
        ]
    )
    blocks = [{"object": "block", "type": "divider", "divider": {}} for _ in range(250)]
    assert "error" in client.append_blocks("abc", blocks)
    assert route.call_count == 2


@respx.mock
def test_append_blocks_reports_how_many_blocks_landed_before_an_error(client):
    """The first chunk is in Notion and cannot be taken back. A caller told only
    "error" would retry the whole body and duplicate those 100 blocks."""
    respx.patch(f"{NOTION_API_URL}/blocks/abc/children").mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"}),
            httpx.Response(429, headers={"Retry-After": "0"}, json={"object": "error", "message": "rate limited"}),
        ]
    )
    blocks = [{"object": "block", "type": "divider", "divider": {}} for _ in range(250)]
    result = client.append_blocks("abc", blocks)
    assert result["appended"] == 100
    assert "error" in result


@respx.mock
def test_append_blocks_reports_zero_appended_when_the_first_chunk_fails(client):
    respx.patch(f"{NOTION_API_URL}/blocks/abc/children").mock(
        return_value=httpx.Response(400, json={"object": "error", "message": "bad block"})
    )
    result = client.append_blocks("abc", [{"object": "block", "type": "divider", "divider": {}}])
    assert result["appended"] == 0


@respx.mock
def test_paginate_stops_following_cursors_once_max_items_is_reached(client):
    """The whole point of a limit: 3 pages exist, 1 row is wanted, 1 request is made."""
    route = respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"id": "1"}], "has_more": True, "next_cursor": "a"}),
            httpx.Response(200, json={"results": [{"id": "2"}], "has_more": True, "next_cursor": "b"}),
            httpx.Response(200, json={"results": [{"id": "3"}], "has_more": False, "next_cursor": None}),
        ]
    )
    assert client.paginate("POST", "/databases/db/query", max_items=1) == {"results": [{"id": "1"}]}
    assert route.call_count == 1


@respx.mock
def test_paginate_asks_for_no_more_rows_than_max_items(client):
    route = respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        return_value=httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})
    )
    client.paginate("POST", "/databases/db/query", max_items=5)
    assert json.loads(route.calls.last.request.content)["page_size"] == 5


@respx.mock
def test_paginate_caps_the_requested_page_size_at_the_api_maximum(client):
    route = respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        return_value=httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})
    )
    client.paginate("POST", "/databases/db/query", max_items=5000)
    assert json.loads(route.calls.last.request.content)["page_size"] == 100


@respx.mock
def test_paginate_trims_a_page_that_overshoots_max_items(client):
    respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "has_more": False, "next_cursor": None}
        )
    )
    assert client.paginate("POST", "/databases/db/query", max_items=2) == {"results": [{"id": "1"}, {"id": "2"}]}


@respx.mock
def test_paginate_makes_no_request_at_all_for_a_zero_max_items(client):
    route = respx.post(f"{NOTION_API_URL}/databases/db/query")
    assert client.paginate("POST", "/databases/db/query", max_items=0) == {"results": []}
    assert route.call_count == 0


@respx.mock
def test_paginate_without_max_items_still_exhausts_every_cursor(client):
    route = respx.post(f"{NOTION_API_URL}/databases/db/query").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"id": "1"}], "has_more": True, "next_cursor": "a"}),
            httpx.Response(200, json={"results": [{"id": "2"}], "has_more": True, "next_cursor": "b"}),
            httpx.Response(200, json={"results": [{"id": "3"}], "has_more": False, "next_cursor": None}),
        ]
    )
    assert len(client.paginate("POST", "/databases/db/query")["results"]) == 3
    assert route.call_count == 3
    assert json.loads(route.calls[0].request.content)["page_size"] == 100


@respx.mock
def test_fetch_block_tree_attaches_children(client):
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/root/children.*").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "kid", "type": "bulleted_list_item", "has_children": True}], "has_more": False, "next_cursor": None},
        )
    )
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/kid/children.*").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "grandkid", "type": "paragraph", "has_children": False}], "has_more": False, "next_cursor": None},
        )
    )
    tree = client.fetch_block_tree("root")
    assert tree[0]["children"][0]["id"] == "grandkid"


@respx.mock
def test_fetch_block_tree_skips_a_child_block_that_has_no_id(client):
    """A response shape we do not understand must not raise out of the tool."""
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/root/children.*").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"type": "paragraph", "has_children": True}], "has_more": False, "next_cursor": None},
        )
    )
    tree = client.fetch_block_tree("root")
    assert tree == [{"type": "paragraph", "has_children": True}]


@respx.mock
def test_fetch_block_tree_stops_at_max_depth(client):
    respx.get(url__regex=rf"{NOTION_API_URL}/blocks/root/children.*").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "kid", "type": "paragraph", "has_children": True}], "has_more": False, "next_cursor": None},
        )
    )
    kid = respx.get(url__regex=rf"{NOTION_API_URL}/blocks/kid/children.*")
    tree = client.fetch_block_tree("root", max_depth=1)
    assert "children" not in tree[0]
    assert kid.call_count == 0
