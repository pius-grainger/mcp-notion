import functools
import inspect
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


_EXPECTED = (ResolutionError, MarkdownError, PropertyError, RuntimeError)


def _fail(exc: Exception) -> dict:
    """Any expected failure as a plain error dict. Candidates ride along when present."""
    if isinstance(exc, MarkdownError):
        return {"error": f"Line {exc.line}: {exc.message}"}
    message = getattr(exc, "message", None) or str(exc)
    candidates = getattr(exc, "candidates", None)
    return {"error": message, "candidates": candidates} if candidates else {"error": message}


def _unexpected(tool: str, exc: Exception) -> dict:
    """A failure nobody anticipated, named well enough to be reportable."""
    return {"error": f"{tool} failed unexpectedly: {type(exc).__name__}: {exc}"}


def tool_boundary(func):
    """Guarantee a tool returns an error value rather than raising.

    The invariant that no exception crosses the MCP boundary cannot rest on
    each tool enumerating the exception types it expects: the first unforeseen
    one — an unexpected shape in a Notion response, say — escapes as a raised
    exception. This is the backstop, and it is now the only place the invariant
    is enforced; the tools no longer each repeat it.

    functools.wraps keeps __name__, __doc__ and __wrapped__, so FastMCP still
    derives the tool's name, schema, and description from the original function.
    """
    annotation = inspect.signature(func).return_annotation
    returns_list = getattr(annotation, "__origin__", annotation) is list

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            error = _fail(exc) if isinstance(exc, _EXPECTED) else _unexpected(func.__name__, exc)
            return [error] if returns_list else error

    return wrapper


def _page_url(page_id: str) -> str:
    """A page id is a valid notion.so URL, and a valid ref for a later call."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _schema_of(database_id: str) -> dict:
    """The database node, or a dict with an "error" key. Callers need both halves."""
    return _get_client().request("GET", f"/databases/{database_id}")


@mcp.tool()
@tool_boundary
def list_databases() -> list[dict]:
    """
    List Notion databases visible to this integration, as {title, url, alias}.
    `alias` is the name configured via NOTION_ALIAS_*, or null. Aliases that
    match no visible database are listed with a null title — they are still
    usable as a `ref`, but the integration may not have access yet.
    """
    nodes = _get_resolver().search("", "database")

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
@tool_boundary
def get_database_schema(ref: str) -> dict:
    """
    Property names and types for a database, as {title, url, properties}.
    Call this before filtering with query_database or writing with update_row.
    ref: an alias, a notion.so URL, an id, or an exact database title.
    """
    database_id = _get_resolver().database(ref)
    node = _schema_of(database_id)
    if "error" in node:
        return node
    return {**fmt.database(node), "properties": fmt.schema(node)}


@mcp.tool()
@tool_boundary
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
    database_id = _get_resolver().database(ref)
    node = _schema_of(database_id)
    if "error" in node:
        return [node]

    schema = fmt.schema(node)
    payload: dict = {}
    clause = build_filter(filter, schema)
    if clause:
        payload["filter"] = clause
    sorts = build_sorts(sort, schema)
    if sorts:
        payload["sorts"] = sorts

    # The limit bounds the fetch, not just the answer: paginating a large
    # database to exhaustion only to discard all but `limit` rows is a pile of
    # round trips and a real chance of hitting the rate limit.
    body = _get_client().paginate(
        "POST", f"/databases/{database_id}/query", payload, max_items=max(limit, 0)
    )
    if "error" in body:
        return [body]
    return [fmt.page(row) for row in body["results"]]


@mcp.tool()
@tool_boundary
def get_page(ref: str) -> dict:
    """
    A Notion page, as {title, url, properties, markdown}. The body is markdown;
    block types outside the supported subset appear as
    `<!-- unsupported: TYPE -->`.
    ref: an alias, a notion.so URL, an id, or an exact page title.
    """
    page_id = _get_resolver().page(ref)
    node = _get_client().request("GET", f"/pages/{page_id}")
    if "error" in node:
        return node

    blocks = _get_client().fetch_block_tree(page_id)
    if isinstance(blocks, dict):
        return blocks
    return {**fmt.page(node), "markdown": blocks_to_markdown(blocks)}


@mcp.tool()
@tool_boundary
def search(query: str, kind: str | None = None) -> list[dict]:
    """
    Search titles of pages and databases shared with this integration, as
    {title, url, kind}. Use it to find the URL to pass as a `ref`.
    Returns at most 25 matches; narrow the query if you expect more.
    kind: "page", "database", or omitted for both.
    """
    if kind not in (None, "page", "database"):
        return [{"error": f"kind must be 'page', 'database', or omitted, not '{kind}'."}]
    nodes = _get_resolver().search(query, kind, max_items=DEFAULT_LIMIT)
    return [
        {"title": fmt.entity_title(node), "url": node.get("url"), "kind": node.get("object")}
        for node in nodes
    ]


@mcp.tool()
@tool_boundary
def create_page(
    parent_ref: str, title: str, markdown: str | None = None, properties: dict | None = None
) -> dict:
    """
    Create a page, either as a row in a database or as a subpage of another page,
    as {title, url, properties}.
    parent_ref: an alias, a notion.so URL, an id, or an exact title.
    title: the new page's title. For a database parent, set the title this way,
      not through properties.
    markdown: optional page body. Headings 1-3, paragraphs, bulleted and
      numbered lists, to-dos, fenced code, quotes, and dividers are supported;
      anything else fails the call before Notion is touched.
    properties: for a database parent only, property name -> value, excluding
      the title property. Call get_database_schema first for the valid names.
    """
    kind, parent_id = _get_resolver().any(parent_ref)
    blocks = markdown_to_blocks(markdown) if markdown else []

    if kind == "database":
        node = _schema_of(parent_id)
        if "error" in node:
            return node
        schema = fmt.schema(node)
        title_name = next((name for name, prop in schema.items() if prop["type"] == "title"), None)
        if title_name is None:
            return {"error": "That database has no title property, so a page cannot be created in it."}
        if properties and title_name in properties:
            return {
                "error": (
                    f"'{title_name}' is this database's title property. Set it with the "
                    "title parameter, not properties."
                )
            }
        payload = write_properties(properties or {}, schema)
        payload[title_name] = write_value(title, "title", title_name)
        parent = {"database_id": parent_id}
    else:
        if properties:
            return {"error": "properties apply only to database rows. A subpage takes a title and a body."}
        # A page-parented page has one property, keyed by the literal "title".
        payload = {"title": {"title": rich_text(title)}}
        parent = {"page_id": parent_id}

    body: dict = {"parent": parent, "properties": payload}
    if blocks:
        body["children"] = blocks[:MAX_CHILDREN_ON_CREATE]

    created = _get_client().request("POST", "/pages", json=body)
    if "error" in created:
        return created

    overflow = blocks[MAX_CHILDREN_ON_CREATE:]
    if overflow:
        # POST /pages caps children at 100; the rest is a normal append.
        appended = _get_client().append_blocks(created["id"], overflow)
        if "error" in appended:
            return {**fmt.page(created), "error": f"Page created, but part of the body failed: {appended['error']}"}
    return fmt.page(created)


@mcp.tool()
@tool_boundary
def append_to_page(ref: str, markdown: str) -> dict:
    """
    Append markdown to the end of a page, as {appended, url}. Existing content is
    never modified or removed.
    ref: an alias, a notion.so URL, an id, or an exact page title.
    markdown: same supported subset as create_page. Empty or whitespace-only
      markdown is rejected without contacting Notion.
    """
    page_id = _get_resolver().page(ref)
    blocks = markdown_to_blocks(markdown)

    if not blocks:
        return {"error": "Nothing to append: the markdown is empty."}

    result = _get_client().append_blocks(page_id, blocks)
    landed = result.get("appended") or 0
    if "error" in result:
        if not landed:
            return {**result, "url": _page_url(page_id)}
        # Those blocks are already in Notion and there is no undo. Reporting a
        # bare failure invites a retry that silently duplicates them.
        return {
            "appended": landed,
            "url": _page_url(page_id),
            "error": (
                f"Partly appended: {landed} of {len(blocks)} blocks were written to the page "
                f"before the call failed. Do not resend the same markdown — it would duplicate "
                f"those {landed} blocks. Resend only what follows them. Notion said: {result['error']}"
            ),
        }
    return {"appended": landed, "url": _page_url(page_id)}


@mcp.tool()
@tool_boundary
def update_row(ref: str, properties: dict) -> dict:
    """
    Set properties on a database row, as {title, url, properties}. Only the named
    properties change; the page body is untouched.
    ref: an alias, a notion.so URL, an id, or an exact row title.
    properties: property name -> value. Call get_database_schema first for the
      valid names and, for select and status properties, the valid options.
    """
    if not properties:
        return {"error": "No properties given. Pass property name -> value."}
    page_id = _get_resolver().page(ref)
    node = _get_client().request("GET", f"/pages/{page_id}")
    if "error" in node:
        return node

    parent = node.get("parent") or {}
    database_id = parent.get("database_id")
    if not database_id:
        return {"error": "That page is not a database row, so it has no properties to set. Use append_to_page instead."}

    schema_node = _schema_of(database_id)
    if "error" in schema_node:
        return schema_node

    payload = write_properties(properties, fmt.schema(schema_node))
    updated = _get_client().request("PATCH", f"/pages/{page_id}", json={"properties": payload})
    if "error" in updated:
        return updated
    return fmt.page(updated)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
