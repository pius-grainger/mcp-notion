import json

import httpx
import pytest
import respx

from mcp_notion.client import NOTION_API_URL, NotionClient
from mcp_notion.resolve import ResolutionError, Resolver, aliases, extract_id

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


def test_extract_id_accepts_31_char_hex_as_title():
    """31-char hex run should not match and return None."""
    hex31 = "1" * 31
    assert extract_id(f"Notes about {hex31}") is None


def test_extract_id_accepts_32_char_hex_as_uuid():
    """32-char hex run should match and return dashed UUID."""
    hex32 = "1" * 32
    assert extract_id(f"Notes about {hex32}") == "11111111-1111-1111-1111-111111111111"


def test_extract_id_rejects_33_char_hex_run():
    """33+ char hex run should not match and return None (not truncated)."""
    hex33 = "1" * 33
    assert extract_id(f"XXX {hex33} XXX") is None


def test_extract_id_rejects_40_char_hex_run():
    """40-char hex run (like a git SHA prefix) should not match and return None."""
    hex40 = "a" * 40
    assert extract_id(f"Notes about {hex40}") is None


def test_extract_id_takes_the_last_id_when_multiple_in_path():
    """When multiple ids appear in a path, the last one (page id) wins."""
    workspace_id = "12345678abcdef0012345678abcdef00"
    page_id = RAW
    dashed_workspace = "12345678-abcd-ef00-1234-5678abcdef00"
    url = f"https://www.notion.so/{workspace_id}/Page-{page_id}"
    assert extract_id(url) == DASHED  # Should return page_id (last), not workspace_id


def test_extract_id_returns_none_for_hex_like_but_not_32_char_title():
    """A title with hex characters but not exactly 32 chars should return None."""
    assert extract_id("Deadbeef Notes") is None
    assert extract_id("abc123def") is None


def test_aliases_skips_empty_alias_values(monkeypatch):
    """Empty-valued NOTION_ALIAS_* variables should not appear in the result."""
    monkeypatch.setenv("NOTION_ALIAS_EMPTY", "")
    monkeypatch.setenv("NOTION_ALIAS_FILLED", RAW)
    result = aliases()
    assert "empty" not in result
    assert result["filled"] == RAW


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


@respx.mock
def test_any_raises_on_a_client_error_instead_of_misreporting_a_page(resolver):
    """A 401 (or any non-404 error) must not be silently reclassified as "page" -
    that would send create_page a wrong-shaped parent payload for what may well
    be a database."""
    respx.get(f"{NOTION_API_URL}/databases/{DASHED}").mock(
        return_value=httpx.Response(401, json={"object": "error", "message": "bad token"})
    )
    with pytest.raises(ResolutionError):
        resolver.any(f"https://notion.so/{RAW}")


def test_any_raises_on_a_transport_failure_instead_of_misreporting_a_page(resolver):
    with respx.mock:
        respx.get(f"{NOTION_API_URL}/databases/{DASHED}").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(ResolutionError):
            resolver.any(f"https://notion.so/{RAW}")


@respx.mock
def test_a_failed_resolution_is_not_cached(resolver):
    """A ref that fails to resolve must be retried, not stuck failing forever -
    the cache stores only successful resolutions."""
    route = respx.post(f"{NOTION_API_URL}/search").mock(return_value=search_response([]))
    with pytest.raises(ResolutionError):
        resolver.page("Spec")

    # The page is now shared with the integration and shows up in search.
    route.mock(return_value=search_response([page_node("Spec", RAW, "https://notion.so/a")]))
    assert resolver.page("Spec") == DASHED
    assert route.call_count == 2
