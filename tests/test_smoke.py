import asyncio

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
