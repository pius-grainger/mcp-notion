"""Notion blocks <-> a documented markdown subset. Pure, no I/O.

Supported both ways: headings 1-3, paragraph, bulleted list, numbered list,
to-do, fenced code, quote, divider. Inline: bold, italic, inline code, links.
Anything else is a visible placeholder on read and a hard error on write.
"""

import re

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

        if rendered:
            lines.append("\n".join(indent + line for line in rendered.split("\n")))

        children = block.get("children")
        if children:
            lines.append(blocks_to_markdown(children, depth + 1))

    return "\n\n".join(line for line in lines if line)


_UNSUPPORTED = (
    (re.compile(r"^\s*\|.*\|\s*$"), "table"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "image"),
    (re.compile(r"^\s*<[a-zA-Z/][^>]*>"), "raw HTML"),
)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.*)$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")
_TODO = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_DIVIDER = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")


class MarkdownError(Exception):
    """Raised when markdown contains a construct outside the supported subset.

    Caught at the tool boundary in server.py; never crosses the MCP boundary.
    The whole call fails: a partial write would leave a half-rendered page.
    """

    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.message = message
        self.line = line


def rich_text(content: str) -> list[dict]:
    """Wrap text as rich_text objects, split at Notion's 2000-character limit."""
    content = content or ""
    if not content:
        return []
    return [
        {"type": "text", "text": {"content": content[start : start + MAX_TEXT_LENGTH]}}
        for start in range(0, len(content), MAX_TEXT_LENGTH)
    ]


def _block(kind: str, **payload) -> dict:
    return {"object": "block", "type": kind, kind: payload}


def markdown_to_blocks(markdown: str) -> list[dict]:
    """Convert markdown to Notion blocks. Raises MarkdownError on anything unsupported.

    Inline emphasis is deliberately passed through as literal characters rather
    than parsed into annotations: a half-correct inline parser silently corrupts
    text, and Notion renders markdown-looking text acceptably as-is.
    """
    lines = (markdown or "").split("\n")
    blocks: list[dict] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        number = index + 1
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            language = fence.group(1) or "plain text"
            body: list[str] = []
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise MarkdownError("Unterminated code fence: no closing ```.", number)
            index += 1
            blocks.append(_block("code", rich_text=rich_text("\n".join(body)), language=language))
            continue

        for pattern, name in _UNSUPPORTED:
            if pattern.search(line):
                raise MarkdownError(
                    f"Unsupported markdown: {name}. Supported constructs are headings 1-3, "
                    "paragraphs, bulleted and numbered lists, to-dos, fenced code, quotes, "
                    "and dividers.",
                    number,
                )

        if _DIVIDER.match(line):
            blocks.append(_block("divider"))
        elif (match := _HEADING.match(line)) :
            blocks.append(_block(f"heading_{len(match.group(1))}", rich_text=rich_text(match.group(2))))
        elif (match := _TODO.match(line)) :
            blocks.append(_block("to_do", rich_text=rich_text(match.group(2)), checked=match.group(1).lower() == "x"))
        elif (match := _BULLET.match(line)) :
            blocks.append(_block("bulleted_list_item", rich_text=rich_text(match.group(1))))
        elif (match := _NUMBERED.match(line)) :
            blocks.append(_block("numbered_list_item", rich_text=rich_text(match.group(1))))
        elif (match := _QUOTE.match(line)) :
            blocks.append(_block("quote", rich_text=rich_text(match.group(1))))
        else:
            blocks.append(_block("paragraph", rich_text=rich_text(stripped)))
        index += 1

    return blocks
