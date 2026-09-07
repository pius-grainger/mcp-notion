import pytest

from mcp_notion.markdown import (
    MAX_TEXT_LENGTH,
    MarkdownError,
    blocks_to_markdown,
    markdown_to_blocks,
    plain_text,
    rich_text,
)


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


def test_empty_paragraph_at_depth_zero():
    # Empty paragraph at depth 0 should produce nothing
    result = blocks_to_markdown([block("paragraph", rich_text=[])])
    assert result == ""


def test_empty_nested_paragraph_contributes_nothing():
    # Empty paragraph nested under a parent should not leave stray whitespace
    parent = block("bulleted_list_item", rich_text=[text("outer")])
    parent["has_children"] = True
    parent["children"] = [block("paragraph", rich_text=[])]  # Empty paragraph
    result = blocks_to_markdown([parent])
    # Should be just "- outer" with no trailing whitespace from the empty child
    assert result == "- outer"
    # Verify no stray indent from empty child
    assert not result.endswith(" ")
    assert "\n  " not in result  # No indented empty line


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


def test_quote_and_divider_map_to_blocks():
    # Named distinctly from the Task 3 test_quote_and_divider (blocks_to_markdown
    # direction) above: an identical name here would silently shadow it, since
    # Python keeps only the last def with a given name in a module.
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
