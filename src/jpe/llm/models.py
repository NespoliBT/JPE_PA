"""Lightweight data models for the LLM layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(slots=True)
class Message:
    """A single chat message.

    ``tool_calls`` is set on assistant messages that request tools, and
    ``tool_call_id`` links a ``role="tool"`` result back to its request.
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role}
        # OpenAI expects null content on assistant messages that only call tools.
        data["content"] = self.content or (None if self.tool_calls else "")
        if self.tool_calls:
            data["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        return data


@dataclass(slots=True)
class TextDelta:
    """A streamed fragment of assistant text."""

    text: str


@dataclass(slots=True)
class ToolCalls:
    """Emitted once the model has finished assembling its tool calls."""

    calls: list[ToolCall]


StreamEvent = TextDelta | ToolCalls


@dataclass(slots=True)
class ModelInfo:
    """Subset of OpenRouter's model metadata that we care about."""

    id: str
    name: str = ""
    context_length: int | None = None
    prompt_price: str | None = None
    completion_price: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: object) -> ModelInfo:
        """Build a :class:`ModelInfo` from an SDK model object or a raw dict."""
        get = _getter(payload)
        pricing = get("pricing")
        pricing_get = _getter(pricing)
        context = get("context_length")
        return cls(
            id=str(get("id") or ""),
            name=str(get("name") or ""),
            context_length=context if isinstance(context, int) else None,
            prompt_price=_as_str(pricing_get("prompt")),
            completion_price=_as_str(pricing_get("completion")),
            extra=payload if isinstance(payload, dict) else {},
        )


def _getter(payload: object) -> Callable[[str], object]:
    """Return a uniform ``key -> value`` accessor for dicts and SDK objects."""
    if isinstance(payload, dict):
        return payload.get
    return lambda key: getattr(payload, key, None)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
