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
        inner_value = (value or {}).get(inner)
        if inner == "array":
            # Rollup over relation/people/files: recursively flatten nested properties
            return [read_value(item) for item in inner_value or []]
        if inner == "date":
            # Flatten date values the same way as top-level date property
            if not inner_value:
                return None
            return {"start": inner_value.get("start"), "end": inner_value.get("end")} if inner_value.get("end") else inner_value.get("start")
        return inner_value
    return f"<unsupported property type: {kind}>"


def read_properties(props: dict) -> dict:
    return {name: read_value(prop) for name, prop in (props or {}).items()}


def title_of(props: dict) -> str:
    for prop in (props or {}).values():
        if (prop or {}).get("type") == "title":
            return plain_text(prop.get("title"))
    return ""


WRITABLE = frozenset(
    {"title", "rich_text", "number", "select", "status", "multi_select", "checkbox", "date", "url", "email", "phone_number"}
)

# Equality operator per type. Notion rejects the wrong operator outright, so
# this table is the whole of filter construction.
_FILTER_OPERATOR = {
    "title": "contains",
    "rich_text": "contains",
    "multi_select": "contains",
    "number": "equals",
    "select": "equals",
    "status": "equals",
    "checkbox": "equals",
    "date": "equals",
    "url": "equals",
    "email": "equals",
    "phone_number": "equals",
}


class PropertyError(Exception):
    """Raised when a property name or value cannot be written or filtered.

    Caught at the tool boundary in server.py; never crosses the MCP boundary.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def write_value(value, kind: str, name: str) -> dict:
    """Build the Notion payload for one property. `kind` comes from the schema."""
    if kind not in WRITABLE:
        raise PropertyError(
            f"Property '{name}' is of type '{kind}', which this server does not write. "
            f"Writable types: {', '.join(sorted(WRITABLE))}."
        )

    if kind == "title":
        return {"title": rich_text(value or "")}
    if kind == "rich_text":
        return {"rich_text": rich_text(value or "")}
    if kind in ("select", "status"):
        return {kind: {"name": value} if value is not None else None}
    if kind == "multi_select":
        names = value if isinstance(value, list) else ([value] if value is not None else [])
        return {"multi_select": [{"name": item} for item in names]}
    if kind == "date":
        if value is None:
            return {"date": None}
        if isinstance(value, dict):
            payload = {"start": value.get("start")}
            if value.get("end"):
                payload["end"] = value["end"]
            return {"date": payload}
        return {"date": {"start": value}}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    return {kind: value}


def write_properties(values: dict, schema: dict) -> dict:
    payload = {}
    for name, value in (values or {}).items():
        prop = _require_property(name, schema)
        payload[name] = write_value(value, prop.get("type") or "", name)
    return payload


def build_filter(filters: dict | None, schema: dict) -> dict | None:
    """Property name -> value, AND-combined. Notion's raw filter syntax stays internal."""
    clauses = []
    for name, value in (filters or {}).items():
        prop = _require_property(name, schema)
        kind = prop.get("type") or ""
        operator = _FILTER_OPERATOR.get(kind)
        if operator is None:
            raise PropertyError(
                f"Property '{name}' is of type '{kind}', which this server does not filter on. "
                f"Filterable types: {', '.join(sorted(_FILTER_OPERATOR))}."
            )
        clauses.append({"property": name, kind: {operator: value}})

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"and": clauses}


def build_sorts(sort: dict | None, schema: dict) -> list[dict]:
    if not sort:
        return []
    name = sort.get("property")
    _require_property(name, schema)
    direction = "descending" if str(sort.get("direction", "asc")).lower().startswith("desc") else "ascending"
    return [{"property": name, "direction": direction}]


def _require_property(name: str, schema: dict) -> dict:
    """Look up `name` in `schema`, raising PropertyError with the valid names if absent."""
    prop = (schema or {}).get(name)
    if prop is None:
        raise PropertyError(f"'{name}' is not a property of this database. Valid names: {_names(schema)}.")
    return prop


def _names(schema: dict) -> str:
    return ", ".join(sorted(schema or {}))
