"""Notion blocks <-> a documented markdown subset. Pure, no I/O.

Supported both ways: headings 1-3, paragraph, bulleted list, numbered list,
to-do, fenced code, quote, divider. Inline: bold, italic, inline code, links.
Anything else is a visible placeholder on read and a hard error on write.
"""

MAX_TEXT_LENGTH = 2000
INDENT = "  "

_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "paragraph": "",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
}


def plain_text(rich: list[dict] | None) -> str:
    """Concatenated text with annotations dropped. Used for titles."""
    return "".join(item.get("plain_text") or (item.get("text") or {}).get("content") or "" for item in rich or [])


def _annotated(rich: list[dict] | None) -> str:
    out = []
    for item in rich or []:
        content = (item.get("text") or {}).get("content")
        if content is None:
            content = item.get("plain_text") or ""
        annotations = item.get("annotations") or {}
        if annotations.get("code"):
            content = f"`{content}`"
        if annotations.get("bold"):
            content = f"**{content}**"
        if annotations.get("italic"):
            content = f"*{content}*"
        link = (item.get("text") or {}).get("link") or {}
        url = link.get("url") or item.get("href")
        if url:
            content = f"[{content}]({url})"
        out.append(content)
    return "".join(out)


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
    """Render blocks as markdown. Nested children are indented two spaces per level."""
    indent = INDENT * depth
    lines = []
    for block in blocks or []:
        kind = block.get("type") or ""
        payload = block.get(kind) or {}

        if kind == "divider":
            rendered = "---"
        elif kind == "code":
            language = payload.get("language") or ""
            rendered = f"```{language}\n{plain_text(payload.get('rich_text'))}\n```"
        elif kind == "to_do":
            mark = "x" if payload.get("checked") else " "
            rendered = f"- [{mark}] {_annotated(payload.get('rich_text'))}"
        elif kind in _PREFIX:
            rendered = f"{_PREFIX[kind]}{_annotated(payload.get('rich_text'))}"
        else:
            # Visible, not silently dropped: the model can see something is there.
            rendered = f"<!-- unsupported: {kind} -->"

        lines.append("\n".join(indent + line for line in rendered.split("\n")))

        children = block.get("children")
        if children:
            lines.append(blocks_to_markdown(children, depth + 1))

    return "\n\n".join(line for line in lines if line)
