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
    assert fmt.entity_title({}) == ""


def test_schema_includes_multi_select_options():
    db = {
        "properties": {
            "Tags": {
                "type": "multi_select",
                "multi_select": {"options": [{"name": "Important"}, {"name": "Urgent"}]},
            }
        }
    }
    assert fmt.schema(db)["Tags"] == {"type": "multi_select", "options": ["Important", "Urgent"]}


def test_schema_includes_status_options():
    db = {
        "properties": {
            "Status": {
                "type": "status",
                "status": {"options": [{"name": "Not Started"}, {"name": "In Progress"}, {"name": "Complete"}]},
            }
        }
    }
    assert fmt.schema(db)["Status"] == {"type": "status", "options": ["Not Started", "In Progress", "Complete"]}


def test_schema_handles_missing_or_none_options():
    db = {
        "properties": {
            "SelectNoOptions": {"type": "select"},
            "SelectNoneOptions": {"type": "select", "select": None},
            "StatusEmpty": {"type": "status", "status": {}},
        }
    }
    schema = fmt.schema(db)
    assert schema["SelectNoOptions"] == {"type": "select", "options": []}
    assert schema["SelectNoneOptions"] == {"type": "select", "options": []}
    assert schema["StatusEmpty"] == {"type": "status", "options": []}
