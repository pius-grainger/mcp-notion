"""Notion nodes -> flat dicts. Pure functions, no I/O.

Bare UUIDs are deliberately dropped. Every entity carries its `url`, which is
itself a valid `ref` for a later call, so the id stays internal without
costing the model any addressability.
"""

from .markdown import plain_text
from .properties import read_properties, title_of

_OPTION_TYPES = ("select", "multi_select", "status")


def page(node: dict) -> dict:
    props = (node or {}).get("properties") or {}
    return {
        "title": title_of(props),
        "url": (node or {}).get("url"),
        "properties": read_properties(props),
        "created_time": (node or {}).get("created_time"),
        "last_edited_time": (node or {}).get("last_edited_time"),
    }


def database(node: dict) -> dict:
    return {"title": plain_text((node or {}).get("title")), "url": (node or {}).get("url")}


def schema(node: dict) -> dict:
    """Property name -> {type, options?}. Options are what the model may write."""
    out = {}
    for name, prop in ((node or {}).get("properties") or {}).items():
        kind = (prop or {}).get("type") or ""
        entry = {"type": kind}
        if kind in _OPTION_TYPES:
            options = ((prop or {}).get(kind) or {}).get("options") or []
            entry["options"] = [option.get("name") for option in options]
        out[name] = entry
    return out


def entity_title(node: dict) -> str:
    """Search returns pages and databases interleaved; each stores its title differently."""
    if (node or {}).get("object") == "database":
        return plain_text(node.get("title"))
    return title_of((node or {}).get("properties") or {})
