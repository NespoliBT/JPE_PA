"""Shell tool, disabled unless JPE_ALLOW_SHELL is enabled.

The tool is always registered so the model can see it, but ``run`` refuses to
execute unless the setting is on. Enabling it is a deliberate opt-in.

Tier: irreversible when enabled. A shell command can be ``ls`` or ``rm -rf``,
and the tool cannot tell the difference reliably, so it takes the conservative
tier. When disabled it is harmless, but it still reports tier 3 — the tier
describes the capability, not the current configuration.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any, ClassVar

from jpe.config import get_settings
from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult
from jpe.tools.paths import workspace_root
from jpe.tools.registry import register

MAX_OUTPUT_CHARS = 20_000


class RunCommandTool(Tool):
    """Run a shell command inside the workspace."""

    name = "run_command"
    description = (
        "Run a shell command in the workspace and return its output. Disabled "
        "unless JPE_ALLOW_SHELL=true."
    )
    tier = Tier.IRREVERSIBLE
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command line to execute."},
            "timeout": {
                "type": "number",
                "minimum": 1,
                "description": "Seconds before the command is killed.",
            },
        },
        "required": ["command"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        settings = get_settings()
        if not settings.allow_shell:
            return ToolResult(
                content=(
                    "Shell access is disabled. Set JPE_ALLOW_SHELL=true to enable "
                    "the run_command tool."
                ),
                is_error=True,
            )

        command = str(kwargs["command"]).strip()
        if not command:
            return ToolResult(content="command must not be empty", is_error=True)

        timeout = float(kwargs.get("timeout") or settings.shell_timeout)
        cwd = workspace_root(settings)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return ToolResult(content=f"Could not start command: {exc}", is_error=True)

        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(
                content=f"Command timed out after {timeout:g}s: {shlex.quote(command)}",
                is_error=True,
            )

        output = stdout.decode("utf-8", errors="replace")
        truncated = len(output) > MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS] + "\n... output truncated"

        code = process.returncode or 0
        header = f"exit code {code}\n"
        return ToolResult(
            content=header + (output or "(no output)"),
            is_error=code != 0,
            metadata={"exit_code": code, "truncated": truncated, "cwd": str(cwd)},
            untrusted_source=f"run_command:{shlex.quote(command)}",
        )


register(RunCommandTool())
