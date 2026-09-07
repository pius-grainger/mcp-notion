import asyncio
import inspect

from mcp_notion import server

EXPECTED_TOOLS = {
    "list_databases",
    "get_database_schema",
    "query_database",
    "get_page",
    "search",
    "create_page",
    "append_to_page",
    "update_row",
}


def test_server_exposes_a_named_mcp_instance():
    assert server.mcp.name == "Notion"


def test_main_is_callable():
    assert callable(server.main)


def test_exactly_the_eight_expected_tools_are_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_every_tool_documents_itself():
    tools = asyncio.run(server.mcp.list_tools())
    assert all(tool.description and tool.description.strip() for tool in tools)


def test_every_tool_is_wrapped_by_the_tool_boundary():
    """The invariant is enforced in one place, so it must actually be applied
    everywhere: an unwrapped tool can raise past the MCP boundary."""
    for name in EXPECTED_TOOLS:
        assert hasattr(getattr(server, name), "__wrapped__"), name


def test_the_tool_boundary_preserves_each_tool_signature():
    """FastMCP derives the exposed schema from the function's signature and
    docstring; a decorator that loses them would publish (*args, **kwargs)."""
    assert list(inspect.signature(server.query_database).parameters) == ["ref", "filter", "sort", "limit"]
    assert server.query_database.__name__ == "query_database"
    assert "Rows of a Notion database" in server.query_database.__doc__


def test_every_tool_still_exposes_its_parameters():
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name["query_database"].inputSchema["properties"]) == {"ref", "filter", "sort", "limit"}
    assert set(by_name["append_to_page"].inputSchema["properties"]) == {"ref", "markdown"}
    assert "kwargs" not in str(by_name["create_page"].inputSchema)
