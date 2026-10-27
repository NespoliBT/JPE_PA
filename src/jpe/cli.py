"""Command-line interface for jpe."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table

from jpe import __version__
from jpe.agent import Agent, ToolSink
from jpe.config import get_settings
from jpe.llm.client import ChatClient
from jpe.llm.models import Message
from jpe.memory.store import TreeNode, get_store
from jpe.scope import ConfirmationBroker, ScopeOfAutonomy
from jpe.soul import build_system_prompt, load_policy, load_soul
from jpe.tools.registry import discover, get_registry

app = typer.Typer(
    name="jpe",
    help="A personal assistant with explicit authority boundaries.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

ModelOption = Annotated[
    str | None,
    typer.Option("--model", "-m", help="Override the model slug."),
]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"jpe {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version.",
        ),
    ] = False,
) -> None:
    """Personal assistant CLI."""


@app.command()
def ask(
    prompt: Annotated[str, typer.Argument(help="Question or instruction for the assistant.")],
    model: ModelOption = None,
    stream: Annotated[
        bool,
        typer.Option("--stream/--no-stream", help="Stream the response."),
    ] = True,
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Let the assistant call registered tools."),
    ] = True,
) -> None:
    """Ask a single question and print the answer."""
    client = ChatClient()
    if stream:
        asyncio.run(_ask_once(client, prompt, model, tools))
    else:
        answer = asyncio.run(client.complete(prompt, model=model))
        console.print(Markdown(answer))


@app.command()
def chat(
    model: ModelOption = None,
    tools: Annotated[
        bool,
        typer.Option("--tools/--no-tools", help="Let the assistant call registered tools."),
    ] = True,
) -> None:
    """Start an interactive chat session. Use 'exit' or Ctrl-D to quit."""
    asyncio.run(_chat_loop(model, tools))


@app.command()
def models(
    search: Annotated[
        str | None,
        typer.Option("--search", "-s", help="Filter by substring."),
    ] = None,
) -> None:
    """List models available on OpenRouter."""
    client = ChatClient()
    available = asyncio.run(client.list_models())
    if search:
        needle = search.lower()
        available = [m for m in available if needle in m.id.lower() or needle in m.name.lower()]
    table = Table(title="OpenRouter models", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("context", justify="right")
    table.add_column("prompt $/tok", justify="right")
    for info in sorted(available, key=lambda m: m.id):
        table.add_row(info.id, str(info.context_length or "-"), info.prompt_price or "-")
    console.print(table)


@app.command()
def tools() -> None:
    """List registered tools and their tiers."""
    discover()
    registry = get_registry()
    table = Table(title="Registered tools")
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("tier", justify="center")
    table.add_column("description")
    for tool in registry.all():
        colour = {0: "green", 1: "blue", 2: "yellow", 3: "red"}[int(tool.tier)]
        table.add_row(tool.name, f"[{colour}]{tool.tier.label}[/{colour}]", tool.description)
    console.print(table)


@app.command()
def policy() -> None:
    """Show the governance document and the identity document."""
    settings = get_settings()
    console.print("[bold]POLICY.md[/bold] [dim](system authority)[/dim]")
    console.print(Markdown(load_policy(settings) or "_(not found)_"))
    console.print()
    console.print("[bold]SOUL.md[/bold] [dim](guideline authority)[/dim]")
    console.print(Markdown(load_soul(settings) or "_(not found)_"))


@app.command()
def memory(
    search: Annotated[
        str | None,
        typer.Option("--search", "-s", help="Filter memories by text."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", "-t", help="Only show memories with this tag."),
    ] = None,
    show: Annotated[
        str | None,
        typer.Option("--show", help="Print the full content of a memory slug."),
    ] = None,
    tree: Annotated[
        bool,
        typer.Option("--tree", help="Show memories as a hierarchy."),
    ] = False,
    forget: Annotated[
        str | None,
        typer.Option("--forget", help="Soft-delete a memory slug (recoverable)."),
    ] = None,
    restore: Annotated[
        str | None,
        typer.Option("--restore", help="Restore a forgotten memory slug."),
    ] = None,
) -> None:
    """List, search, or read stored memories."""
    store = get_store()

    if forget:
        if store.forget(forget):
            console.print(f"[yellow]Forgot {forget!r}[/yellow] [dim](recoverable)[/dim]")
        else:
            console.print(f"[red]No memory with slug {forget!r}[/red]")
            raise typer.Exit(code=1)
        return

    if restore:
        if store.restore(restore):
            console.print(f"[green]Restored {restore!r}[/green]")
        else:
            console.print(f"[red]No forgotten memory with slug {restore!r}[/red]")
            raise typer.Exit(code=1)
        return

    if show:
        body = store.read(show)
        if body is None:
            console.print(f"[red]No memory with slug {show!r}[/red]")
            raise typer.Exit(code=1)
        item = store.get(show)
        if item is not None:
            ancestors = store.ancestors(item.slug)
            if ancestors:
                console.print(f"[dim]Path: {' > '.join([*ancestors, item.slug])}[/dim]")
            if item.links:
                console.print(f"[dim]Links: {', '.join(item.links)}[/dim]")
            backlinks = store.backlinks(item.slug)
            if backlinks:
                console.print(
                    f"[dim]Linked from: {', '.join(entry.slug for entry in backlinks)}[/dim]"
                )
        console.print(Markdown(body))
        return

    if tree:
        nodes = store.tree()
        if not nodes:
            console.print("[dim]No memories yet.[/dim]")
            return
        for line in _render_tree(nodes):
            console.print(line, markup=False, highlight=False)
        return

    memories = store.search(search) if search else store.list_all(tag)
    if tag and search:
        memories = [m for m in memories if tag in m.tags]

    if not memories:
        console.print("[dim]No memories yet.[/dim]")
        return

    table = Table(title=f"Memories ({len(memories)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("title")
    table.add_column("tags", style="green")
    table.add_column("parent", style="magenta")
    table.add_column("updated", style="dim")
    for item in memories:
        table.add_row(
            item.slug,
            item.title,
            ", ".join(item.tags) or "-",
            item.parent or "-",
            item.updated_at,
        )
    console.print(table)


def _render_tree(nodes: list[TreeNode], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        tags = f" [{', '.join(node.memory.tags)}]" if node.memory.tags else ""
        lines.append(f"{'  ' * depth}- {node.memory.slug}: {node.memory.title}{tags}")
        lines.extend(_render_tree(node.children, depth + 1))
    return lines


class _MarkdownStream:
    """Render streamed text as markdown, re-rendering as chunks arrive."""

    def __init__(self, target: Console) -> None:
        self.console = target
        self._parts: list[str] = []
        self._live: Live | None = None

    def start(self) -> None:
        self._parts = []
        self._live = Live(
            Markdown(""),
            console=self.console,
            refresh_per_second=15,
            vertical_overflow="ellipsis",
            transient=True,
        )
        self._live.start()

    def write(self, chunk: str) -> None:
        self._parts.append(chunk)
        if self._live is not None:
            self._live.update(Markdown("".join(self._parts)))

    def flush(self) -> None:
        """Stop the preview and print the final markdown once."""
        text = "".join(self._parts)
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._parts = []
        if text.strip():
            self.console.print(Markdown(text))


MAX_ARG_CHARS = 120


def _format_args(arguments: dict[str, object]) -> str:
    """Render tool arguments compactly, truncating long values."""
    parts: list[str] = []
    for key, value in arguments.items():
        text = repr(value)
        if len(text) > MAX_ARG_CHARS:
            text = text[:MAX_ARG_CHARS] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _print_tool(name: str, arguments: dict[str, object], icon: str = "→") -> None:
    console.print(f"[dim]{icon} {name}({_format_args(arguments)})[/dim]")
    console.print()


def _make_callbacks(renderer: _MarkdownStream, tools: bool) -> ToolSink | None:
    """Build the tool callback that cooperates with live rendering."""
    if not tools:
        return None

    def on_tool(name: str, arguments: dict[str, object]) -> None:
        renderer.flush()
        _print_tool(name, arguments, _tool_icon(name))
        renderer.start()

    return on_tool


def _tool_icon(name: str) -> str:
    """Return the glyph registered for a tool, defaulting to an arrow."""
    try:
        return get_registry().get(name).icon
    except KeyError:
        return "→"


def _make_agent(client: ChatClient, tools: bool) -> Agent:
    registry = discover() if tools else get_registry()
    if not get_settings().allow_shell:
        registry = registry.subset(lambda tool: tool.name != "run_command")
    settings = get_settings()
    return Agent(
        client,
        registry,
        scope=ScopeOfAutonomy(),
        confirmations=ConfirmationBroker(ttl=timedelta(seconds=settings.confirmation_ttl_seconds)),
    )


async def _make_agent_with_mcp(client: ChatClient, tools: bool) -> Agent:
    """Build an agent, connecting any configured MCP servers first.

    MCP connections are opened here and closed by :func:`_close_agent`, because
    the SDK's transports are bound to the task that opened them.
    """
    registry = discover() if tools else get_registry()
    settings = get_settings()
    if not settings.allow_shell:
        registry = registry.subset(lambda tool: tool.name != "run_command")

    if tools and settings.mcp_servers:
        from jpe.mcp import configs_from_settings

        for config in configs_from_settings(settings.mcp_servers):
            try:
                await registry.add_mcp(config)
            except Exception as exc:  # a broken server must not kill the session
                console.print(f"[yellow]MCP server {config.name!r} unavailable: {exc}[/yellow]")

    return Agent(
        client,
        registry,
        scope=ScopeOfAutonomy(),
        confirmations=ConfirmationBroker(ttl=timedelta(seconds=settings.confirmation_ttl_seconds)),
    )


async def _close_agent(agent: Agent) -> None:
    await agent.registry.close_mcp()


def _system_message() -> Message:
    return Message(role="system", content=build_system_prompt())


async def _ask_once(
    client: ChatClient,
    prompt: str,
    model: str | None,
    tools: bool,
) -> None:
    agent = await _make_agent_with_mcp(client, tools)
    messages = [_system_message(), Message(role="user", content=prompt)]
    renderer = _MarkdownStream(console)
    on_tool = _make_callbacks(renderer, tools)

    renderer.start()
    try:
        await agent.run(messages, model=model, on_text=renderer.write, on_tool=on_tool)
    finally:
        renderer.flush()
        await _close_agent(agent)


async def _chat_loop(model: str | None, tools: bool) -> None:
    """Run the interactive session inside a single event loop.

    ``AsyncOpenAI`` binds its HTTP connection pool to the loop that created it,
    so calling ``asyncio.run`` per turn would leave the client pointing at a
    closed loop after the first exchange.
    """
    client = ChatClient()
    agent = await _make_agent_with_mcp(client, tools)
    history: list[Message] = [_system_message()]
    console.print("[dim]Interactive chat. Type 'exit' to quit.[/dim]")
    while True:
        try:
            prompt = await asyncio.to_thread(console.input, "[bold cyan]you>[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if prompt.strip().lower() in {"exit", "quit"}:
            break
        if not prompt.strip():
            continue
        history.append(Message(role="user", content=prompt))
        console.print("[bold green]jpe>[/bold green]")
        renderer = _MarkdownStream(console)
        on_tool = _make_callbacks(renderer, tools)

        renderer.start()
        try:
            result = await agent.run(
                history,
                model=model,
                on_text=renderer.write,
                on_tool=on_tool,
            )
        finally:
            renderer.flush()
        history = result.messages
        console.print()

    await _close_agent(agent)


if __name__ == "__main__":
    app()
