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
