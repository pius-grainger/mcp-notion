# mcp-notion

An MCP server exposing eight narrow Notion tools. Reads pages and databases,
writes additively. Nothing is deleted, archived, or overwritten.

## Setup

1. Create an internal integration at <https://www.notion.com/my-integrations>
   and copy its token.
2. `cp .env.example .env` and set `NOTION_API_KEY`.
3. **Share each page or database with the integration.** In Notion, open it,
   then ••• > Connections > your integration. Without this, every call returns
   a 404 — the token alone grants no access.
4. Install: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`

Optional aliases, so a database can be named rather than pasted:

```
NOTION_ALIAS_TASKS=https://www.notion.so/workspace/0123456789abcdef0123456789abcdef
```

`list_databases` reports configured aliases.

## Registering with Claude Code

```bash
claude mcp add notion -- /absolute/path/to/mcp-notion/.venv/bin/mcp-notion
```

## Refs

Every tool takes a `ref`, resolved in this order: configured alias, notion.so
URL, page or database id, exact title. An ambiguous title is an error listing
candidate URLs — the server never guesses.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_databases` | Databases visible to the integration, with aliases |
| `get_database_schema` | Property names, types, and select options |
| `query_database` | Rows, filtered and sorted by property name |
| `get_page` | Page properties plus the body as markdown |
| `search` | Title search across shared pages and databases |
| `create_page` | New database row or subpage, with an optional markdown body |
| `append_to_page` | Append markdown to the end of a page |
| `update_row` | Set properties on a database row |

## Markdown subset

Both directions: headings 1-3, paragraphs, bulleted and numbered lists, to-dos,
fenced code, quotes, dividers. Reading an unsupported block yields
`<!-- unsupported: TYPE -->`. Writing an unsupported construct — tables, images,
raw HTML — fails the whole call, naming the construct and the line.

Inline markup is one-way. Bold, italic, code, and links are rendered as
markdown when reading, but written back as literal characters: appending
`**bold**` writes those eight characters, not bold text. A half-correct inline
parser would silently corrupt text, which is worse than a visible asterisk.

## Tests

```bash
.venv/bin/pytest
```

No network, no API key required.
