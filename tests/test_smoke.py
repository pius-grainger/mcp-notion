from mcp_notion import server


def test_server_exposes_a_named_mcp_instance():
    assert server.mcp.name == "Notion"


def test_main_is_callable():
    assert callable(server.main)
