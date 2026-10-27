"""Thin wrapper around the OpenAI SDK pointed at OpenRouter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, cast

from openai import AsyncOpenAI

from jpe.config import Settings, get_settings
from jpe.llm.models import Message, ModelInfo, StreamEvent, TextDelta, ToolCall, ToolCalls


class _CompletionsLike(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class _ChatLike(Protocol):
    completions: _CompletionsLike


class _ModelsLike(Protocol):
    async def list(self) -> Any: ...


class _ClientLike(Protocol):
    chat: _ChatLike
    models: _ModelsLike


class ChatClient:
    """Async chat client for OpenRouter.

    The underlying SDK client can be injected, which keeps the class testable
    without network access.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        client: _ClientLike | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client: _ClientLike = client or cast(
            "_ClientLike",
            AsyncOpenAI(
                api_key=self.settings.require_api_key(),
                base_url=self.settings.base_url,
                default_headers={
                    "HTTP-Referer": "https://github.com/nespoli/jpe",
                    "X-Title": "jpe personal assistant",
                },
            ),
        )

    def _payload(
        self,
        messages: Sequence[Message],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": self.settings.temperature if temperature is None else temperature,
            "stream": stream,
        }
        tokens = self.settings.max_tokens if max_tokens is None else max_tokens
        if tokens is not None:
            payload["max_tokens"] = tokens
        if tools:
            payload["tools"] = list(tools)
        return payload

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Send a single prompt and return the full response text."""
        messages = self.build_messages([], prompt, system_prompt)
        response = await self._client.chat.completions.create(
            **self._payload(messages, model, temperature, max_tokens, stream=False)
        )
        return _extract_text(response)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield text deltas, then a single :class:`ToolCalls` if requested.

        Tool call fragments arrive spread across chunks, so they are
        accumulated by index and emitted once the stream ends.
        """
        response = await self._client.chat.completions.create(
            **self._payload(messages, model, temperature, max_tokens, stream=True, tools=tools)
        )
        pending: dict[int, ToolCall] = {}
        async for chunk in response:
            text = _extract_delta(chunk)
            if text:
                yield TextDelta(text)
            for index, call in _extract_tool_call_deltas(chunk):
                current = pending.setdefault(index, ToolCall(id="", name=""))
                if call.id:
                    current.id = call.id
                if call.name:
                    current.name = call.name
                current.arguments += call.arguments
        calls = [pending[i] for i in sorted(pending)]
        if calls:
            yield ToolCalls(calls)

    async def list_models(self) -> list[ModelInfo]:
        """Return the models advertised by the API."""
        response = await self._client.models.list()
        data = getattr(response, "data", response)
        return [ModelInfo.from_api(item) for item in data]

    def build_messages(
        self,
        history: Sequence[Message],
        prompt: str,
        system_prompt: str | None = None,
    ) -> list[Message]:
        system = system_prompt if system_prompt is not None else self.settings.system_prompt
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.extend(history)
        messages.append(Message(role="user", content=prompt))
        return messages


def _extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def _extract_delta(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    return content if isinstance(content, str) else ""


def _extract_tool_call_deltas(chunk: Any) -> list[tuple[int, ToolCall]]:
    """Pull partial tool calls out of a streamed chunk, keyed by index."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return []
    delta = getattr(choices[0], "delta", None)
    raw_calls = getattr(delta, "tool_calls", None) or []
    extracted: list[tuple[int, ToolCall]] = []
    for raw in raw_calls:
        function = getattr(raw, "function", None)
        extracted.append(
            (
                int(getattr(raw, "index", 0) or 0),
                ToolCall(
                    id=getattr(raw, "id", None) or "",
                    name=getattr(function, "name", None) or "",
                    arguments=getattr(function, "arguments", None) or "",
                ),
            )
        )
    return extracted
