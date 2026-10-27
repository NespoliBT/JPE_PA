"""Core tool abstractions.

A tool is a named, described capability with a JSON-schema parameter
definition, a :class:`~jpe.tiers.Tier`, and an async ``run`` method. The schema
is OpenAI-tool-calling compatible, so tools can be handed directly to the model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from jpe.authority import wrap_untrusted
from jpe.tiers import Tier


@dataclass(slots=True)
class ToolResult:
    """Outcome of a tool invocation.

    ``untrusted_source`` marks the result as data rather than instructions. When
    set, the agent loop wraps ``content`` in an ``<untrusted>`` region before it
    reaches the model. Tools that return content the user did not author —
    fetched pages, file bodies, MCP results — must set it.
    """

    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    untrusted_source: str | None = None

    def for_model(self) -> str:
        """Return the content as the model should see it."""
        if self.untrusted_source is None:
            return self.content
        return wrap_untrusted(self.content, self.untrusted_source)


class Tool(ABC):
    """Base class for assistant capabilities."""

    name: str
    description: str
    #: JSON schema for the tool's arguments.
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    #: How reversible this tool's side effects are. Defaults to read-only.
    tier: ClassVar[Tier] = Tier.READ
    #: Short glyph shown in the transcript when the tool runs.
    icon: ClassVar[str] = "→"

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated keyword arguments."""

    def to_openai_schema(self) -> dict[str, Any]:
        """Return an OpenAI ``tools`` entry describing this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
