"""Filesystem tools: read, write, edit, list, and search.

All paths are confined to ``JPE_WORKSPACE`` by :mod:`jpe.tools.paths`.

Reads are marked untrusted: a file's contents are data, not instructions.
Writes are tier 1 (reversible by the assistant when the workspace is
git-tracked).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from jpe.config import get_settings
from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult
from jpe.tools.paths import SandboxError, resolve_path
from jpe.tools.registry import register

MAX_MATCHES = 200
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "result", ".mypy_cache", ".ruff_cache"}


def _error(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


def _check_size(path: Path, limit: int) -> str | None:
    if path.exists() and path.stat().st_size > limit:
        return f"File {path.name!r} is larger than the {limit} byte limit"
    return None


class ReadFileTool(Tool):
    """Read a text file, optionally a line range."""

    name = "read_file"
    description = (
        "Read a text file from the workspace. Use start_line/end_line to read a "
        "slice of a large file instead of the whole thing."
    )
    tier = Tier.READ
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root."},
            "start_line": {"type": "integer", "minimum": 1, "description": "First line (1-based)."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Last line, inclusive."},
        },
        "required": ["path"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        try:
            path = resolve_path(str(kwargs["path"]), settings)
        except SandboxError as exc:
            return _error(str(exc))
        if not path.is_file():
            return _error(f"Not a file: {kwargs['path']}")
        if (problem := _check_size(path, settings.max_file_bytes)) is not None:
            return _error(problem)

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return _error(f"Could not read {kwargs['path']}: {exc}")

        start = int(kwargs.get("start_line") or 1)
        end = int(kwargs.get("end_line") or len(lines))
        if start < 1 or end < start:
            return _error(f"Invalid line range {start}-{end}")
        selected = lines[start - 1 : end]
        body = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(selected))
        return ToolResult(
            content=body or "(empty)",
            metadata={"path": str(path), "lines": len(selected), "total_lines": len(lines)},
            untrusted_source=f"read_file:{kwargs['path']}",
        )


class WriteFileTool(Tool):
    """Create or overwrite a file."""

    name = "write_file"
    description = (
        "Create or overwrite a text file in the workspace. Prefer edit_file for "
        "changing existing files so unrelated content is preserved."
    )
    tier = Tier.REVERSIBLE
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        try:
            path = resolve_path(str(kwargs["path"]), settings)
        except SandboxError as exc:
            return _error(str(exc))

        content = str(kwargs.get("content", ""))
        size = len(content.encode("utf-8"))
        if size > settings.max_file_bytes:
            return _error(f"Content is {size} bytes, over the {settings.max_file_bytes} limit")

        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _error(f"Could not write {kwargs['path']}: {exc}")

        action = "Overwrote" if existed else "Created"
        return ToolResult(
            content=f"{action} {kwargs['path']} ({size} bytes)",
            metadata={"path": str(path), "bytes": size, "created": not existed},
        )


class EditFileTool(Tool):
    """Replace an exact string in a file."""

    name = "edit_file"
    description = (
        "Replace an exact string in a file. old_string must appear exactly once "
        "unless replace_all is true. Include surrounding context to stay unique."
    )
    tier = Tier.REVERSIBLE
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root."},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring exactly one.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        try:
            path = resolve_path(str(kwargs["path"]), settings)
        except SandboxError as exc:
            return _error(str(exc))
        if not path.is_file():
            return _error(f"Not a file: {kwargs['path']}")
        if (problem := _check_size(path, settings.max_file_bytes)) is not None:
            return _error(problem)

        old = str(kwargs["old_string"])
        new = str(kwargs["new_string"])
        if not old:
            return _error("old_string must not be empty")

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return _error(f"Could not read {kwargs['path']}: {exc}")

        count = text.count(old)
        if count == 0:
            return _error("old_string not found in file")
        replace_all = bool(kwargs.get("replace_all"))
        if count > 1 and not replace_all:
            return _error(f"old_string appears {count} times; add context or set replace_all=true")

        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return _error(f"Could not write {kwargs['path']}: {exc}")

        replaced = count if replace_all else 1
        return ToolResult(
            content=f"Replaced {replaced} occurrence(s) in {kwargs['path']}",
            metadata={"path": str(path), "replacements": replaced},
        )


class ListDirTool(Tool):
    """List directory entries."""

    name = "list_dir"
    description = "List files and directories under a workspace path."
    tier = Tier.READ
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path. Defaults to the root."},
            "recursive": {"type": "boolean", "description": "Walk subdirectories."},
        },
        "required": [],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        try:
            path = resolve_path(str(kwargs.get("path") or "."), settings)
        except SandboxError as exc:
            return _error(str(exc))
        if not path.is_dir():
            return _error(f"Not a directory: {kwargs.get('path')}")

        recursive = bool(kwargs.get("recursive"))
        entries: list[str] = []
        try:
            if recursive:
                for item in sorted(path.rglob("*")):
                    if any(part in SKIP_DIRS for part in item.relative_to(path).parts):
                        continue
                    suffix = "/" if item.is_dir() else ""
                    entries.append(item.relative_to(path).as_posix() + suffix)
                    if len(entries) >= MAX_MATCHES:
                        break
            else:
                for item in sorted(path.iterdir()):
                    entries.append(item.name + ("/" if item.is_dir() else ""))
        except OSError as exc:
            return _error(f"Could not list {kwargs.get('path')}: {exc}")

        return ToolResult(
            content="\n".join(entries) or "(empty directory)",
            metadata={"path": str(path), "count": len(entries)},
            untrusted_source=f"list_dir:{kwargs.get('path') or '.'}",
        )


class SearchFilesTool(Tool):
    """Search file contents with a regular expression."""

    name = "search_files"
    description = (
        "Search file contents in the workspace with a regular expression. "
        "Returns matching lines with file and line number."
    )
    tier = Tier.READ
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "Directory to search. Defaults to root."},
            "glob": {"type": "string", "description": "Only search files matching this glob."},
        },
        "required": ["pattern"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        try:
            root = resolve_path(str(kwargs.get("path") or "."), settings)
        except SandboxError as exc:
            return _error(str(exc))
        if not root.is_dir():
            return _error(f"Not a directory: {kwargs.get('path')}")

        try:
            regex = re.compile(str(kwargs["pattern"]))
        except re.error as exc:
            return _error(f"Invalid regular expression: {exc}")

        glob = str(kwargs.get("glob") or "*")
        matches: list[str] = []
        for item in sorted(root.rglob(glob)):
            if not item.is_file():
                continue
            if any(part in SKIP_DIRS for part in item.relative_to(root).parts):
                continue
            if _check_size(item, settings.max_file_bytes) is not None:
                continue
            try:
                text = item.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    relative = item.relative_to(root).as_posix()
                    matches.append(f"{relative}:{number}: {line.strip()}")
                    if len(matches) >= MAX_MATCHES:
                        break
            if len(matches) >= MAX_MATCHES:
                break

        return ToolResult(
            content="\n".join(matches) or "(no matches)",
            metadata={"count": len(matches), "root": str(root)},
            untrusted_source=f"search_files:{kwargs.get('path') or '.'}",
        )


register(ReadFileTool())
register(WriteFileTool())
register(EditFileTool())
register(ListDirTool())
register(SearchFilesTool())
