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


def test_rollup_with_array_inner_type_flattens_nested_properties():
    # Rollup over a relation property: inner type is "array" with raw relation properties
    result = read_value({"type": "rollup", "rollup": {"type": "array",
        "array": [{"type": "relation", "relation": [{"id": "11111111-1111-1111-1111-111111111111"}]}]}})
    # Result should be a list of flattened properties, no bare UUIDs
    assert result == ["1 related items"]
    assert "11111111-1111-1111-1111-111111111111" not in str(result)


def test_rollup_with_empty_array_inner_type():
    result = read_value({"type": "rollup", "rollup": {"type": "array", "array": []}})
    assert result == []


def test_rollup_with_date_inner_type_bare_start():
    result = read_value({"type": "rollup", "rollup": {"type": "date",
        "date": {"start": "2026-09-07", "end": None}}})
    assert result == "2026-09-07"


def test_rollup_with_date_inner_type_range():
    result = read_value({"type": "rollup", "rollup": {"type": "date",
        "date": {"start": "2026-09-07", "end": "2026-09-09"}}})
    assert result == {"start": "2026-09-07", "end": "2026-09-09"}


def test_rollup_with_date_inner_type_none():
    result = read_value({"type": "rollup", "rollup": {"type": "date", "date": None}})
    assert result is None


def test_formula_and_rollup_scalar_inner_types_pass_through():
    # Ensure scalar types still pass through unchanged for formulas and rollups
    assert read_value({"type": "formula", "formula": {"type": "string", "string": "result"}}) == "result"
    assert read_value({"type": "formula", "formula": {"type": "number", "number": 42}}) == 42
    assert read_value({"type": "formula", "formula": {"type": "boolean", "boolean": True}}) is True
    assert read_value({"type": "rollup", "rollup": {"type": "string", "string": "text"}}) == "text"
    assert read_value({"type": "rollup", "rollup": {"type": "number", "number": 99}}) == 99
    assert read_value({"type": "rollup", "rollup": {"type": "boolean", "boolean": False}}) is False


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
