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
