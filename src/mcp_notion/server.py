import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP(
    "Notion",
    instructions=(
        "Reads and writes Notion pages and databases. Pages and databases are "
        "addressed by a `ref`: a configured alias, a notion.so URL, a page id, "
        "or an exact title. Call list_databases to discover aliases and "
        "get_database_schema before filtering or writing properties. Page "
        "content is markdown. Writes are additive: nothing is deleted, "
        "archived, or overwritten."
    ),
)

from . import format as fmt
from .client import NotionClient
from .markdown import MarkdownError, blocks_to_markdown, markdown_to_blocks, rich_text
from .properties import PropertyError, build_filter, build_sorts, write_properties, write_value
from .resolve import ResolutionError, Resolver, aliases, extract_id

DEFAULT_LIMIT = 25
MAX_CHILDREN_ON_CREATE = 100

_client: NotionClient | None = None
_resolver: Resolver | None = None


def _get_client() -> NotionClient:
    global _client
    if _client is None:
        key = os.environ.get("NOTION_API_KEY")
        if not key:
            raise RuntimeError("NOTION_API_KEY environment variable is not set")
        _client = NotionClient(api_key=key)
    return _client


def _get_resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        _resolver = Resolver(_get_client())
    return _resolver


def _fail(exc: Exception) -> dict:
    """Any expected failure as a plain error dict. Candidates ride along when present."""
    if isinstance(exc, MarkdownError):
        return {"error": f"Line {exc.line}: {exc.message}"}
    message = getattr(exc, "message", None) or str(exc)
    candidates = getattr(exc, "candidates", None)
    return {"error": message, "candidates": candidates} if candidates else {"error": message}


def _page_url(page_id: str) -> str:
    """A page id is a valid notion.so URL, and a valid ref for a later call."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _schema_of(database_id: str) -> dict:
    """The database node, or a dict with an "error" key. Callers need both halves."""
    return _get_client().request("GET", f"/databases/{database_id}")


@mcp.tool()
def list_databases() -> list[dict]:
    """
    List Notion databases visible to this integration, as {title, url, alias}.
    `alias` is the name configured via NOTION_ALIAS_*, or null. Aliases that
    match no visible database are listed with a null title — they are still
    usable as a `ref`, but the integration may not have access yet.
    """
    try:
        nodes = _get_resolver().search("", "database")
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]

    configured = {name: extract_id(value) for name, value in aliases().items()}
    by_id = {}
    listed = []
    for node in nodes:
        entry = fmt.database(node)
        entry["alias"] = None
        by_id[extract_id(node.get("id"))] = entry
        listed.append(entry)

    for name, database_id in configured.items():
        if database_id in by_id:
            by_id[database_id]["alias"] = name
        else:
            # An alias value that yields no id (a title-like string, not a
            # URL or id) has no URL to offer either. The alias name itself
            # is still a valid `ref`, so it stays listed.
            url = _page_url(database_id) if database_id else None
            listed.append({"title": None, "url": url, "alias": name})
    return listed


@mcp.tool()
def get_database_schema(ref: str) -> dict:
    """
    Property names and types for a database, as {title, url, properties}.
    Call this before filtering with query_database or writing with update_row.
    ref: an alias, a notion.so URL, an id, or an exact database title.
    """
    try:
        database_id = _get_resolver().database(ref)
    except (ResolutionError, RuntimeError) as e:
        return _fail(e)

    node = _schema_of(database_id)
    if "error" in node:
        return node
    return {**fmt.database(node), "properties": fmt.schema(node)}


@mcp.tool()
def query_database(
    ref: str, filter: dict | None = None, sort: dict | None = None, limit: int = DEFAULT_LIMIT
) -> list[dict]:
    """
    Rows of a Notion database, as {title, url, properties}.
    ref: an alias, a notion.so URL, an id, or an exact database title.
    filter: property name -> value, combined with AND. Text properties match by
      substring, everything else by equality. Names must come from
      get_database_schema.
    sort: {"property": name, "direction": "asc" | "desc"}.
    limit: maximum rows to return.
    """
    try:
        database_id = _get_resolver().database(ref)
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]

    node = _schema_of(database_id)
    if "error" in node:
        return [node]

    schema = fmt.schema(node)
    try:
        payload: dict = {}
        clause = build_filter(filter, schema)
        if clause:
            payload["filter"] = clause
        sorts = build_sorts(sort, schema)
        if sorts:
            payload["sorts"] = sorts
    except PropertyError as e:
        return [_fail(e)]

    body = _get_client().paginate("POST", f"/databases/{database_id}/query", payload)
    if "error" in body:
        return [body]
    return [fmt.page(row) for row in body["results"][: max(limit, 0)]]


@mcp.tool()
def get_page(ref: str) -> dict:
    """
    A Notion page, as {title, url, properties, markdown}. The body is markdown;
    block types outside the supported subset appear as
    `<!-- unsupported: TYPE -->`.
    ref: an alias, a notion.so URL, an id, or an exact page title.
    """
    try:
        page_id = _get_resolver().page(ref)
    except (ResolutionError, RuntimeError) as e:
        return _fail(e)

    node = _get_client().request("GET", f"/pages/{page_id}")
    if "error" in node:
        return node

    blocks = _get_client().fetch_block_tree(page_id)
    if isinstance(blocks, dict):
        return blocks
    return {**fmt.page(node), "markdown": blocks_to_markdown(blocks)}


@mcp.tool()
def search(query: str, kind: str | None = None) -> list[dict]:
    """
    Search titles of pages and databases shared with this integration, as
    {title, url, kind}. Use it to find the URL to pass as a `ref`.
    Returns at most 25 matches; narrow the query if you expect more.
    kind: "page", "database", or omitted for both.
    """
    if kind not in (None, "page", "database"):
        return [{"error": f"kind must be 'page', 'database', or omitted, not '{kind}'."}]
    try:
        nodes = _get_resolver().search(query, kind)
    except (ResolutionError, RuntimeError) as e:
        return [_fail(e)]
    return [
        {"title": fmt.entity_title(node), "url": node.get("url"), "kind": node.get("object")}
        for node in nodes[:DEFAULT_LIMIT]
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
