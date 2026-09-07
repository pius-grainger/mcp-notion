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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
