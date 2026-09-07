"""Notion typed property values <-> plain Python. Pure, no I/O.

Reading flattens: a model does not benefit from Notion's nesting. Writing
consults the database schema, because the correct payload shape depends
entirely on the property's declared type.
"""

from .markdown import plain_text, rich_text

_SCALAR = ("number", "checkbox", "url", "email", "phone_number", "created_time", "last_edited_time")


def read_value(prop: dict) -> object:
    """One Notion property value as a plain Python value."""
    kind = (prop or {}).get("type") or ""
    value = (prop or {}).get(kind)

    if kind in ("title", "rich_text"):
        return plain_text(value)
    if kind in _SCALAR:
        return value
    if kind in ("select", "status"):
        return (value or {}).get("name")
    if kind in ("multi_select", "people"):
        return [item.get("name") for item in value or []]
    if kind == "date":
        if not value:
            return None
        return {"start": value.get("start"), "end": value.get("end")} if value.get("end") else value.get("start")
    if kind == "relation":
        # Relations are lists of UUIDs, which no tool accepts back. A count is
        # the honest, useful summary; the page itself carries the detail.
        return f"{len(value or [])} related items"
    if kind in ("formula", "rollup"):
        inner = (value or {}).get("type") or ""
        return (value or {}).get(inner)
    return f"<unsupported property type: {kind}>"


def read_properties(props: dict) -> dict:
    return {name: read_value(prop) for name, prop in (props or {}).items()}


def title_of(props: dict) -> str:
    for prop in (props or {}).values():
        if (prop or {}).get("type") == "title":
            return plain_text(prop.get("title"))
    return ""
