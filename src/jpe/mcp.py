"""MCP host: connect to servers, expose their tools as local tools.

MCP is a client-server protocol. ``jpe`` is the *host*: it coordinates one
client per server, lists that server's tools, and proxies calls. Remote tools
are wrapped in :class:`McpTool`, which implements the same ``Tool`` ABC as the
builtins, so the agent loop is unchanged.

Three details from the spec shape this module:

- **Tool names are only unique per server.** Two servers may both expose
  ``search``. Names are therefore namespaced as ``server.tool``.
- **Two error channels.** Protocol errors (unknown tool, malformed request) come
  back as ``MCPError`` and the model can rarely fix them. Tool execution errors
  come back as ``is_error=True`` and the model *can* self-correct. The first is
  raised, the second is mapped to ``ToolResult(is_error=True)``.
- **Servers are arbitrary code with access to your accounts.** A server must be
  explicitly configured, and its tools get a tier from the server's annotations
  where present, else from the configured default.

See ``research-v2.md`` §4.1.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult

#: How long to wait for a server to start and answer ``initialize``.
DEFAULT_STARTUP_TIMEOUT = 30.0
#: How long to wait for a single ``tools/call``.
DEFAULT_CALL_TIMEOUT = 60.0


@dataclass(slots=True)
class McpServerConfig:
    """How to reach one MCP server.

    ``transport`` is ``stdio`` for a local subprocess or ``http`` for a remote
    Streamable HTTP endpoint. ``default_tier`` applies to tools whose annotations
    do not say enough — a server that exposes no hints is assumed to be able to
    change things, so the default is deliberately not ``READ``.
    """

    name: str
    transport: Literal["stdio", "http"] = "stdio"
    #: stdio only.
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    #: http only.
    url: str = ""
    headers: dict[str, str] | None = None
    #: Tier for tools without usable annotations.
    default_tier: Tier = Tier.COSTLY
    #: Per-tool tier overrides, keyed by the *unqualified* tool name.
    tool_tiers: dict[str, Tier] = field(default_factory=dict)
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    call_timeout: float = DEFAULT_CALL_TIMEOUT

    def __post_init__(self) -> None:
        if not self.name:
            msg = "McpServerConfig requires a name"
            raise ValueError(msg)
        if self.transport == "stdio" and not self.command:
            msg = f"MCP server {self.name!r}: stdio transport requires a command"
            raise ValueError(msg)
        if self.transport == "http" and not self.url:
            msg = f"MCP server {self.name!r}: http transport requires a url"
            raise ValueError(msg)


def tier_from_annotations(annotations: Any, default: Tier) -> Tier:
    """Infer a tier from MCP tool annotations.

    The hints are advisory, so the mapping is conservative: a tool that says it
    is read-only is trusted as such, a tool that says it is destructive is
    treated as irreversible, and anything else falls back to the server default.
    """
    if annotations is None:
        return default
    if getattr(annotations, "destructive_hint", None):
        return Tier.IRREVERSIBLE
    if getattr(annotations, "read_only_hint", None):
        return Tier.READ
    return default


def _content_to_text(content: list[Any]) -> str:
    """Flatten MCP content blocks into text.

    Text blocks are concatenated. Non-text blocks (images, resources) are
    described rather than dropped, so the model knows something was there.
    """
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        kind = getattr(block, "type", None) or type(block).__name__
        parts.append(f"[{kind} content omitted]")
    return "\n".join(parts)


class McpConnection:
    """A live connection to one MCP server.

    The session lives inside an ``AsyncExitStack`` because the SDK's transports
    are async context managers tied to a task group. That means the connection
    must be opened and closed from the same task — which is why
    :meth:`connect` and :meth:`close` are explicit rather than per-call.
    """

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: list[Any] = []

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> list[Any]:
        """Open the connection, initialize, and return the server's tools."""
        if self._session is not None:
            return self._tools

        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self.config.startup_timeout):
                if self.config.transport == "stdio":
                    params = StdioServerParameters(
                        command=self.config.command,
                        args=list(self.config.args),
                        env=self.config.env,
                        cwd=self.config.cwd,
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                else:
                    import httpx2
                    from mcp.client.streamable_http import streamable_http_client

                    http_client = httpx2.AsyncClient(headers=self.config.headers or {})
                    await stack.enter_async_context(http_client)
                    read, write = await stack.enter_async_context(
                        streamable_http_client(self.config.url, http_client=http_client)
                    )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
        except Exception:
            await stack.aclose()
            raise

        self._stack = stack
        self._session = session
        self._tools = list(listed.tools)
        return self._tools

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a remote tool, mapping its two error channels."""
        if self._session is None:
            msg = f"MCP server {self.name!r} is not connected"
            raise RuntimeError(msg)

        try:
            async with asyncio.timeout(self.config.call_timeout):
                result = await self._session.call_tool(tool_name, arguments)
        except TimeoutError:
            return ToolResult(
                content=f"MCP tool {self.name}.{tool_name} timed out after "
                f"{self.config.call_timeout:g}s",
                is_error=True,
            )
        except Exception as exc:
            # Protocol error: unknown tool, malformed request, transport failure.
            # The model cannot fix these, so they surface as an error result
            # rather than crashing the loop.
            return ToolResult(
                content=f"MCP protocol error calling {self.name}.{tool_name}: {exc}",
                is_error=True,
                metadata={"protocol_error": True},
            )

        text = _content_to_text(list(result.content or []))
        return ToolResult(
            content=text or "(empty result)",
            is_error=bool(result.is_error),
            metadata={"server": self.name, "tool": tool_name},
            # Remote content is data, never instructions.
            untrusted_source=f"mcp:{self.name}/{tool_name}",
        )

    async def close(self) -> None:
        """Close the connection and release the subprocess."""
        stack, self._stack = self._stack, None
        self._session = None
        self._tools = []
        if stack is not None:
            await stack.aclose()


class McpTool(Tool):
    """A remote MCP tool, presented as a local :class:`Tool`.

    ``name``, ``parameters``, ``tier``, and ``icon`` are class attributes on
    :class:`Tool`, but an MCP tool's values are only known at runtime. Rather
    than weaken the base class, each instance is built from a small generated
    subclass that carries the right class attributes.
    """

    connection: McpConnection
    remote_name: str

    def __init__(
        self,
        connection: McpConnection,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        tier: Tier,
    ) -> None:
        self.connection = connection
        self.remote_name = remote_name
        # ``name``, ``description``, ``parameters``, ``tier``, and ``icon`` come
        # from the generated subclass; see :func:`_mcp_tool_class`.
        del description, parameters, tier

    async def run(self, **kwargs: Any) -> ToolResult:
        return await self.connection.call(self.remote_name, kwargs)


def _mcp_tool_class(
    name: str,
    description: str,
    parameters: dict[str, Any],
    tier: Tier,
) -> type[McpTool]:
    """Build a :class:`McpTool` subclass carrying this tool's static metadata."""
    return type(
        "McpToolInstance",
        (McpTool,),
        {
            "name": name,
            "description": description,
            "parameters": parameters,
            "tier": tier,
            "icon": "🔌",
        },
    )


def build_tools(connection: McpConnection, tools: list[Any]) -> list[McpTool]:
    """Wrap a server's tool descriptions as :class:`McpTool` instances."""
    config = connection.config
    built: list[McpTool] = []
    for remote in tools:
        remote_name = str(remote.name)
        tier = config.tool_tiers.get(remote_name)
        if tier is None:
            tier = tier_from_annotations(getattr(remote, "annotations", None), config.default_tier)
        schema = getattr(remote, "input_schema", None) or {"type": "object", "properties": {}}
        cls = _mcp_tool_class(
            name=f"{connection.name}.{remote_name}",
            description=str(remote.description or f"MCP tool {remote_name}"),
            parameters=schema,
            tier=tier,
        )
        built.append(cls(connection, remote_name, str(remote.description or ""), schema, tier))
    return built


def configs_from_settings(entries: list[dict[str, Any]]) -> list[McpServerConfig]:
    """Build server configs from the ``JPE_MCP_SERVERS`` JSON list.

    Unknown keys are ignored rather than fatal, so a config written for a later
    version still loads. A malformed entry raises, because silently skipping a
    server the user asked for is worse than refusing to start.
    """
    configs: list[McpServerConfig] = []
    for entry in entries:
        tool_tiers: dict[str, Tier] = {}
        for tool_name, tier_value in (entry.get("tool_tiers") or {}).items():
            tool_tiers[str(tool_name)] = _coerce_tier(tier_value)

        default_tier = _coerce_tier(entry.get("default_tier", Tier.COSTLY))

        transport = str(entry.get("transport") or ("http" if entry.get("url") else "stdio"))
        if transport not in {"stdio", "http"}:
            msg = f"MCP server {entry.get('name')!r}: unknown transport {transport!r}"
            raise ValueError(msg)

        configs.append(
            McpServerConfig(
                name=str(entry.get("name") or ""),
                transport=cast('Literal["stdio", "http"]', transport),
                command=str(entry.get("command") or ""),
                args=[str(arg) for arg in entry.get("args") or []],
                env=entry.get("env") or None,
                cwd=entry.get("cwd") or None,
                url=str(entry.get("url") or ""),
                headers=entry.get("headers") or None,
                default_tier=default_tier,
                tool_tiers=tool_tiers,
                startup_timeout=float(entry.get("startup_timeout") or DEFAULT_STARTUP_TIMEOUT),
                call_timeout=float(entry.get("call_timeout") or DEFAULT_CALL_TIMEOUT),
            )
        )
    return configs


def _coerce_tier(value: Any) -> Tier:
    """Accept a tier as an int, a name, or a ``Tier``."""
    if isinstance(value, Tier):
        return value
    if isinstance(value, int):
        return Tier(value)
    if isinstance(value, str):
        try:
            return Tier[value.strip().upper()]
        except KeyError:
            try:
                return Tier(int(value))
            except ValueError:
                pass
    msg = f"Invalid tier {value!r}; expected 0-3 or read/reversible/costly/irreversible"
    raise ValueError(msg)
