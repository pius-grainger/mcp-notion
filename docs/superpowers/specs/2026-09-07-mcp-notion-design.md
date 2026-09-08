# mcp-notion Design

Date: 2026-09-07

## Purpose

An MCP server exposing a narrow, opinionated set of Notion operations, in
contrast to Notion's official hosted connector, which exposes generic
search/fetch/create over an entire workspace. The tools here are shaped to
concrete workflows: read a spec page, query a task database, append a status
update, set a row's properties.

The server follows the structure already established in `mcp-linear`: a single
network module, pure translation modules around it, and FastMCP tool
definitions that never raise past the tool boundary.

## Non-goals

- Deleting blocks, archiving pages, or replacing an existing page body. Writes
  are additive only, so a bad model call cannot destroy work.
- Full fidelity with Notion's block model. Page content crosses the tool
  boundary as markdown, with a documented supported subset.
- Covering the whole Notion API. Eight tools, added to only when a real
  workflow needs one.

## Architecture

New repository `mcp-notion`, package `src/mcp_notion/`.

| Module | Responsibility |
| --- | --- |
| `client.py` | The only module that touches the network. httpx, base URL `https://api.notion.com/v1`, bearer auth, `Notion-Version` header, cursor pagination, request chunking, uniform error dicts. |
| `resolve.py` | Turns a caller-supplied `ref` into a Notion UUID. Alias, URL, bare UUID, then title search. Caches successful resolutions per process. |
| `markdown.py` | `blocks_to_markdown` and `markdown_to_blocks`. Pure, no I/O. |
| `properties.py` | Notion typed property values to and from plain Python. Reading flattens; writing consults the database schema to build a correct payload. |
| `format.py` | Page and row nodes to flat dicts. Pure. |
| `server.py` | FastMCP tool definitions. |

`properties.py` has no counterpart in `mcp-linear`. Notion property values are
deeply nested and differ per type, and a write must be built against the
database's schema, so the translation earns its own module.

### API version

Pinned to `2022-06-28`. Notion's `2025-09-03` version splits databases into
data sources and moves the query endpoint to `/v1/data_sources/{id}/query`.
That indirection buys nothing for these workflows. The pin is a single
constant in `client.py` and can be raised later.

### Configuration

Read from `.env` via `python-dotenv`, matching `mcp-linear`:

- `NOTION_API_KEY` — internal integration token. Required.
- `NOTION_ALIAS_<NAME>=<url-or-uuid>` — optional named shortcuts for
  frequently used databases and pages. `list_databases` reports them.

## Tool surface

Read:

- `list_databases()` — configured aliases plus the databases the integration
  can see.
- `get_database_schema(ref)` — property names and types. The model calls this
  before filtering or writing.
- `query_database(ref, filter=None, sort=None, limit=...)` — flat rows.
  `filter` is a dict of property name to value, combined with AND, using the
  equality operator appropriate to each property's type; `properties.py`
  translates it against the schema into Notion's filter object. `sort` is
  `{"property": name, "direction": "asc"|"desc"}`. Notion's raw filter
  syntax is deliberately not exposed; a workflow needing OR or nesting is a
  reason to revisit this, not to leak the API shape through the tool.
- `get_page(ref)` — properties plus body as markdown.
- `search(query, kind=None)` — title matches across shared content.

Write (additive only):

- `create_page(parent_ref, title, markdown=None, properties=None)`
- `append_to_page(ref, markdown)`
- `update_row(ref, properties)` — set property values by name.

## Ref resolution

Every tool takes a `ref` rather than a UUID. `resolve.py` tries four forms in
order and takes the first that matches:

1. **Alias** — case-insensitive match against configured `NOTION_ALIAS_*`
   names.
2. **URL** — the trailing 32-hex id is extracted from a `notion.so` URL.
   Handles `Page-Title-<id>`, a bare `/<id>` path, and a `?v=` view suffix.
3. **Bare UUID** — 32 hex characters, dashed or undashed.
4. **Title** — `POST /v1/search`, filtered to page or database as the calling
   tool requires.

The title branch resolves only when exactly one case-insensitive exact title
match exists. Multiple exact matches, or zero exact matches with partial hits,
return `{"error": ..., "candidates": [{"title": ..., "url": ...}]}`. The
server never guesses between candidates; the user disambiguates by pasting
back a URL.

Successful resolutions are cached per process, keyed on the ref string.

## Markdown subset

Supported in both directions, at block level: headings 1-3, paragraph, bulleted
list, numbered list, to-do (`- [ ]` and `- [x]`), fenced code with language,
blockquote, divider. Nested children are recursed to a depth of 3 and rendered
indented.

Inline annotations — bold, italic, inline code, links — are deliberately
asymmetric:

- **Reading** renders a `rich_text` run's annotations as markdown, so a bold
  run comes back as `**bold**` and a linked run as `[text](https://example.com)`.
- **Writing** passes those characters through as literal text. `**bold**` is
  written as one unannotated text run containing the eight characters
  `**bold**`; it is not parsed into a bold annotation.

The asymmetry is the point. A half-correct inline parser silently corrupts
text — an unmatched asterisk, a bracket inside a code span, a parenthesis
inside a URL — and silently corrupting a user's page is worse than a visible
literal asterisk, which Notion renders acceptably as-is. Writing a correct
inline parser is a larger job than the whole rest of this module and buys
little for these workflows.

The consequence, stated plainly because a caller will meet it: inline markup
does not round-trip. Reading a bold run and appending the result back writes
literal `**bold**`, not bold text.

Reading a block type outside the subset yields a visible placeholder line,
`<!-- unsupported: image -->`, rather than dropping content silently.

Writing a construct outside the subset — tables, images, raw HTML — fails the
entire call with an error naming the construct and the line number. There are
no partial writes and no silent drops.

Two Notion limits are handled by the server rather than pushed onto the
caller: appends are chunked at 100 blocks per request, in `client.py`, and any
text run longer than 2000 characters is split across multiple `rich_text`
objects, in `markdown.py` where those objects are constructed.

## Error handling

No exception escapes a tool. Every tool returns `{"error": ...}`; tools with a
list return type return `[{"error": ...}]`. This matches `mcp-linear`.

Cases handled explicitly:

- `NOTION_API_KEY` unset.
- 401 — invalid token.
- 403 or 404 on a well-formed id — the message states that the page or
  database must be shared with the integration. This is Notion's most common
  and most confusing failure mode, and a generic "not found" sends the user
  looking in the wrong place.
- 429 — honor `Retry-After`, retry once, then return an error.
- 400 — surface Notion's own validation message verbatim.
- Ambiguous ref — error with candidates, as above.
- Unknown property name on write — error listing the valid names from the
  database schema.

## Testing

pytest with respx, no network, matching `mcp-linear`'s idiom.

- `markdown.py`: round-trip tests (markdown to blocks to markdown) per
  supported construct; placeholder emission for unsupported block types;
  rejection with line number for unsupported markdown.
- `properties.py`: read and write for each supported property type.
- `resolve.py`: ref parsing per form; single match, multiple exact matches,
  and partial-match-only outcomes of the title branch; cache behavior.
- `client.py`: 401, 429 with retry, non-JSON body, pagination across cursors,
  block chunking, and rich_text splitting.
- Each tool: one happy path and one failure path against mocked routes.
- A smoke test asserting the server imports and registers the eight expected
  tool names.
