"""Example tool: report the current date and time."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult
from jpe.tools.registry import register


class ClockTool(Tool):
    """Return the current time, optionally in a given IANA timezone."""

    name = "clock"
    description = "Get the current date and time, optionally for a specific IANA timezone."
    tier = Tier.READ
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'Europe/Rome'. Defaults to UTC.",
            }
        },
        "required": [],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        tz_name = kwargs.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(str(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult(content=f"Unknown timezone: {tz_name}", is_error=True)
        now = datetime.now(tz)
        return ToolResult(
            content=now.isoformat(timespec="seconds"),
            metadata={"timezone": str(tz_name), "utc": datetime.now(UTC).isoformat()},
        )


register(ClockTool())
