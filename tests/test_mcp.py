"""Tests for the MCP host: config parsing, tier inference, and tool wrapping.

The live connection tests are marked ``live`` and skipped unless
``JPE_MCP_LIVE=1``, because they spawn a subprocess and download a package.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from jpe.mcp import (
    McpConnection,
    McpServerConfig,
    McpTool,
    _content_to_text,
    _mcp_tool_class,
    build_tools,
    configs_from_settings,
    tier_from_annotations,
)
from jpe.tiers import Tier
from jpe.tools.base import ToolResult


class FakeAnnotations:
    def __init__(
        self,
        read_only: bool | None = None,
        destructive: bool | None = None,
    ) -> None:
        self.read_only_hint = read_only
        self.destructive_hint = destructive


class FakeRemoteTool:
    def __init__(
        self,
        name: str,
        description: str = "",
        schema: dict[str, Any] | None = None,
        annotations: Any = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = schema or {"type": "object", "properties": {}}
        self.annotations = annotations


class FakeConnection(McpConnection):
    """A connection that records calls instead of talking to a server."""

    def __init__(self, config: McpServerConfig) -> None:
        super().__init__(config)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = ToolResult(content="ok")

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return self.result


# -- config -------------------------------------------------------------


def test_config_requires_name() -> None:
    with pytest.raises(ValueError, match="requires a name"):
        McpServerConfig(name="", command="x")


def test_stdio_requires_command() -> None:
    with pytest.raises(ValueError, match="requires a command"):
        McpServerConfig(name="s", transport="stdio")


def test_http_requires_url() -> None:
    with pytest.raises(ValueError, match="requires a url"):
        McpServerConfig(name="s", transport="http")


def test_config_defaults_to_costly_tier() -> None:
    """A server with no hints is assumed able to change things."""
    assert McpServerConfig(name="s", command="x").default_tier is Tier.COSTLY


def test_configs_from_settings_stdio() -> None:
    configs = configs_from_settings(
        [{"name": "time", "command": "uvx", "args": ["mcp-server-time"]}]
    )
    assert len(configs) == 1
    assert configs[0].name == "time"
    assert configs[0].transport == "stdio"
    assert configs[0].args == ["mcp-server-time"]


def test_configs_from_settings_infers_http_from_url() -> None:
    configs = configs_from_settings([{"name": "remote", "url": "https://x.example/mcp"}])
    assert configs[0].transport == "http"


def test_configs_from_settings_parses_tiers() -> None:
    configs = configs_from_settings(
        [
            {
                "name": "s",
                "command": "x",
                "default_tier": "reversible",
                "tool_tiers": {"send": "irreversible", "read": 0},
            }
        ]
    )
    assert configs[0].default_tier is Tier.REVERSIBLE
    assert configs[0].tool_tiers["send"] is Tier.IRREVERSIBLE
    assert configs[0].tool_tiers["read"] is Tier.READ


def test_configs_from_settings_rejects_bad_tier() -> None:
    with pytest.raises(ValueError, match="Invalid tier"):
        configs_from_settings([{"name": "s", "command": "x", "default_tier": "nonsense"}])


def test_configs_from_settings_rejects_bad_transport() -> None:
    with pytest.raises(ValueError, match="unknown transport"):
        configs_from_settings([{"name": "s", "command": "x", "transport": "carrier-pigeon"}])


def test_configs_from_settings_empty() -> None:
    assert configs_from_settings([]) == []


# -- tier inference -----------------------------------------------------


def test_tier_read_only_hint() -> None:
    assert tier_from_annotations(FakeAnnotations(read_only=True), Tier.COSTLY) is Tier.READ


def test_tier_destructive_hint_wins() -> None:
    """Destructive beats read-only: a tool claiming both is not trusted."""
    annotations = FakeAnnotations(read_only=True, destructive=True)
    assert tier_from_annotations(annotations, Tier.READ) is Tier.IRREVERSIBLE


def test_tier_falls_back_to_default() -> None:
    assert tier_from_annotations(FakeAnnotations(), Tier.COSTLY) is Tier.COSTLY
    assert tier_from_annotations(None, Tier.COSTLY) is Tier.COSTLY


# -- tool wrapping ------------------------------------------------------


def test_build_tools_namespaces_names() -> None:
    connection = FakeConnection(McpServerConfig(name="time", command="x"))
    tools = build_tools(connection, [FakeRemoteTool("get_current_time")])
    assert tools[0].name == "time.get_current_time"


def test_build_tools_uses_annotations_for_tier() -> None:
    connection = FakeConnection(McpServerConfig(name="s", command="x"))
    tools = build_tools(
        connection,
        [
            FakeRemoteTool("read", annotations=FakeAnnotations(read_only=True)),
            FakeRemoteTool("write", annotations=FakeAnnotations(destructive=True)),
            FakeRemoteTool("unknown"),
        ],
    )
    by_name = {tool.name: tool for tool in tools}
    assert by_name["s.read"].tier is Tier.READ
    assert by_name["s.write"].tier is Tier.IRREVERSIBLE
    assert by_name["s.unknown"].tier is Tier.COSTLY


def test_build_tools_honours_explicit_override() -> None:
    config = McpServerConfig(name="s", command="x", tool_tiers={"danger": Tier.IRREVERSIBLE})
    connection = FakeConnection(config)
    tools = build_tools(
        connection, [FakeRemoteTool("danger", annotations=FakeAnnotations(read_only=True))]
    )
    assert tools[0].tier is Tier.IRREVERSIBLE


def test_build_tools_carries_schema() -> None:
    schema = {"type": "object", "properties": {"tz": {"type": "string"}}}
    connection = FakeConnection(McpServerConfig(name="s", command="x"))
    tools = build_tools(connection, [FakeRemoteTool("t", schema=schema)])
    assert tools[0].parameters == schema
    assert tools[0].to_openai_schema()["function"]["parameters"] == schema


def test_build_tools_sets_icon() -> None:
    connection = FakeConnection(McpServerConfig(name="s", command="x"))
    tools = build_tools(connection, [FakeRemoteTool("t")])
    assert tools[0].icon == "🔌"


def test_mcp_tool_class_is_a_tool() -> None:
    cls = _mcp_tool_class("s.t", "desc", {"type": "object"}, Tier.READ)
    assert issubclass(cls, McpTool)


async def test_mcp_tool_proxies_the_call() -> None:
    connection = FakeConnection(McpServerConfig(name="s", command="x"))
    tools = build_tools(connection, [FakeRemoteTool("t")])
    await tools[0].run(timezone="Europe/Rome")
    assert connection.calls == [("t", {"timezone": "Europe/Rome"})]


# -- content flattening -------------------------------------------------


def test_content_to_text_joins_text_blocks() -> None:
    class Block:
        def __init__(self, text: str) -> None:
            self.text = text

    assert _content_to_text([Block("a"), Block("b")]) == "a\nb"


def test_content_to_text_describes_non_text_blocks() -> None:
    class Image:
        type = "image"

    assert "image content omitted" in _content_to_text([Image()])


def test_content_to_text_empty() -> None:
    assert _content_to_text([]) == ""


# -- live ---------------------------------------------------------------

live = pytest.mark.skipif(
    not os.environ.get("JPE_MCP_LIVE"),
    reason="JPE_MCP_LIVE is not set (spawns a subprocess and downloads a package)",
)


@live
async def test_live_stdio_server_lists_and_calls() -> None:
    from jpe.tools.registry import Registry

    registry = Registry()
    config = McpServerConfig(name="time", command="uvx", args=["mcp-server-time"])
    tools = await registry.add_mcp(config)
    try:
        names = {tool.name for tool in tools}
        assert "time.get_current_time" in names

        result = await registry.get("time.get_current_time").run(timezone="Europe/Rome")
        assert result.is_error is False
        assert "Europe/Rome" in result.content
        assert result.untrusted_source == "mcp:time/get_current_time"
    finally:
        await registry.close_mcp()


@live
async def test_live_tool_error_is_mapped_not_raised() -> None:
    from jpe.tools.registry import Registry

    registry = Registry()
    config = McpServerConfig(name="time", command="uvx", args=["mcp-server-time"])
    await registry.add_mcp(config)
    try:
        result = await registry.get("time.get_current_time").run(timezone="Bad/Zone")
        assert result.is_error is True
        assert "Bad/Zone" in result.content
    finally:
        await registry.close_mcp()
