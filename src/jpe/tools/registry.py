"""Tool registry and discovery helpers."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from jpe.tools.base import Tool

if TYPE_CHECKING:
    from jpe.mcp import McpConnection, McpServerConfig


class Registry:
    """A simple name -> tool mapping."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._mcp: list[McpConnection] = []

    def add(self, tool: Tool) -> Tool:
        if not tool.name:
            msg = f"{type(tool).__name__} must define a non-empty 'name'"
            raise ValueError(msg)
        if tool.name in self._tools:
            msg = f"Tool {tool.name!r} is already registered"
            raise ValueError(msg)
        self._tools[tool.name] = tool
        return tool

    async def add_mcp(self, config: McpServerConfig) -> list[Tool]:
        """Connect to an MCP server and register its tools as ``server.tool``.

        The connection is kept open and closed by :meth:`close_mcp`, because the
        SDK's transports are bound to the task that opened them.
        """
        from jpe.mcp import McpConnection, build_tools

        connection = McpConnection(config)
        remote_tools = await connection.connect()
        built = build_tools(connection, remote_tools)
        for tool in built:
            self.add(tool)
        self._mcp.append(connection)
        return list(built)

    async def close_mcp(self) -> None:
        """Close every MCP connection this registry opened."""
        for connection in self._mcp:
            await connection.close()
        self._mcp.clear()

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            msg = f"Unknown tool {name!r}. Available: {', '.join(sorted(self._tools)) or 'none'}"
            raise KeyError(msg) from None

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def subset(self, predicate: Callable[[Tool], bool]) -> Registry:
        """Return a new registry containing only tools matching ``predicate``."""
        selected = Registry()
        for tool in self._tools.values():
            if predicate(tool):
                selected.add(tool)
        return selected

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


_registry = Registry()


def get_registry() -> Registry:
    """Return the process-wide registry."""
    return _registry


def register(tool: Tool) -> Tool:
    """Register a tool instance in the global registry."""
    return _registry.add(tool)


def discover(package: str = "jpe.tools.builtin") -> Registry:
    """Import every module under ``package`` so registrations run."""
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__):
        importlib.import_module(f"{package}.{info.name}")
    return _registry
