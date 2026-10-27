"""Tests for the tool registry and builtin tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jpe.config import get_settings
from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult
from jpe.tools.registry import Registry, discover, get_registry


class EchoTool(Tool):
    name = "echo"
    description = "Echo the input back."

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(content=str(kwargs.get("text", "")))


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JPE_WORKSPACE", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_registry_add_and_get() -> None:
    registry = Registry()
    tool = registry.add(EchoTool())
    assert registry.get("echo") is tool
    assert len(registry) == 1


def test_registry_rejects_duplicates() -> None:
    registry = Registry()
    registry.add(EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.add(EchoTool())


def test_registry_unknown_tool() -> None:
    with pytest.raises(KeyError, match="Unknown tool"):
        Registry().get("nope")


def test_openai_schema_shape() -> None:
    schema = EchoTool().to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"


def test_tools_default_to_read_tier() -> None:
    assert EchoTool().tier is Tier.READ


def test_discover_registers_builtins() -> None:
    discover()
    names = {tool.name for tool in get_registry()}
    assert {"clock", "read_file", "write_file", "fetch_url", "run_command"} <= names


def test_builtin_tiers() -> None:
    discover()
    registry = get_registry()
    assert registry.get("read_file").tier is Tier.READ
    assert registry.get("write_file").tier is Tier.REVERSIBLE
    assert registry.get("edit_file").tier is Tier.REVERSIBLE
    assert registry.get("run_command").tier is Tier.IRREVERSIBLE


async def test_clock_tool_returns_iso_timestamp() -> None:
    discover()
    result = await get_registry().get("clock").run(timezone="Europe/Rome")
    assert result.is_error is False
    assert "T" in result.content


async def test_clock_tool_rejects_bad_timezone() -> None:
    discover()
    result = await get_registry().get("clock").run(timezone="Not/AZone")
    assert result.is_error is True


async def test_read_file_marks_output_untrusted(workspace: Path) -> None:
    (workspace / "notes.md").write_text("hello")
    discover()
    result = await get_registry().get("read_file").run(path="notes.md")
    assert result.untrusted_source == "read_file:notes.md"
    assert "<untrusted" in result.for_model()


async def test_write_file_is_not_untrusted(workspace: Path) -> None:
    discover()
    result = await get_registry().get("write_file").run(path="out.md", content="x")
    assert result.untrusted_source is None
    assert result.for_model() == result.content


async def test_read_file_rejects_path_outside_workspace(workspace: Path) -> None:
    discover()
    result = await get_registry().get("read_file").run(path="/etc/passwd")
    assert result.is_error is True
    assert "outside the workspace" in result.content


async def test_read_file_rejects_denied_glob(workspace: Path) -> None:
    (workspace / ".env").write_text("SECRET=1")
    discover()
    result = await get_registry().get("read_file").run(path=".env")
    assert result.is_error is True
    assert "denied pattern" in result.content


async def test_search_files_marks_output_untrusted(workspace: Path) -> None:
    (workspace / "a.py").write_text("needle = 1\n")
    discover()
    result = await get_registry().get("search_files").run(pattern="needle")
    assert "a.py:1" in result.content
    assert result.untrusted_source is not None


async def test_run_command_refuses_when_disabled(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JPE_ALLOW_SHELL", "false")
    get_settings.cache_clear()
    discover()
    result = await get_registry().get("run_command").run(command="echo hi")
    assert result.is_error is True
    assert "disabled" in result.content
