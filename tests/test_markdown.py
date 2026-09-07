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
