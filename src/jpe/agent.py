"""Agent loop: stream a reply, run any requested tools, then continue.

The loop is where authority is enforced. Two gates sit between the model and a
tool:

1. **Scope** — is this tool inside the declared bounds, and is the scope still
   live? (:class:`~jpe.scope.ScopeOfAutonomy`)
2. **Confirmation** — for tier-3 tools, has the user said yes to *this exact
   call*? (:class:`~jpe.scope.ConfirmationBroker`)

A refusal is returned to the model as an error result, not raised. The model is
expected to explain the refusal to the user and, for tier 3, ask for the token.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from jpe.llm.client import ChatClient
from jpe.llm.models import Message, TextDelta, ToolCall, ToolCalls
from jpe.scope import ConfirmationBroker, ScopeOfAutonomy
from jpe.tiers import Tier
from jpe.tools.base import ToolResult
from jpe.tools.registry import Registry

# Called with each streamed text fragment so the CLI can render incrementally.
TextSink = Callable[[str], None]
# Called with (tool name, arguments) before a tool runs, for user feedback.
ToolSink = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class AgentResult:
    """Final state of one agent run."""

    text: str
    messages: list[Message] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


def _confirmation_message(pending_token: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """The refusal text handed back to the model for a tier-3 call."""
    rendered = json.dumps(arguments, sort_keys=True, default=str)
    return (
        f"CONFIRMATION REQUIRED for {tool_name}({rendered}).\n"
        "This action is irreversible or externally visible, so it did not run.\n"
        f"Ask the user to confirm, and include the token {pending_token} in your "
        "question. The call will run only if the user's next message contains "
        "that token. Do not invent a token and do not retry without one."
    )


class Agent:
    """Drives a conversation, executing tools until the model stops asking."""

    def __init__(
        self,
        client: ChatClient,
        registry: Registry,
        *,
        scope: ScopeOfAutonomy | None = None,
        confirmations: ConfirmationBroker | None = None,
        max_steps: int = 8,
    ) -> None:
        self.client = client
        self.registry = registry
        self.scope = scope or ScopeOfAutonomy()
        self.confirmations = confirmations or ConfirmationBroker()
        self.max_steps = max_steps

    async def run(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        on_text: TextSink | None = None,
        on_tool: ToolSink | None = None,
    ) -> AgentResult:
        """Run the loop, returning the final assistant text.

        ``messages`` is not mutated; the returned ``AgentResult.messages``
        contains the full transcript including tool calls and results.
        """
        transcript = list(messages)
        collected: list[ToolResult] = []
        tools = self.registry.schemas() or None

        # A user message may carry a confirmation token. Redeem before the
        # model sees the turn, so a retried tier-3 call can succeed.
        self._redeem_from_transcript(transcript)

        for _ in range(self.max_steps):
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            async for event in self.client.stream(transcript, model=model, tools=tools):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    if on_text:
                        on_text(event.text)
                elif isinstance(event, ToolCalls):
                    calls = event.calls

            text = "".join(text_parts)
            if not calls:
                transcript.append(Message(role="assistant", content=text))
                return AgentResult(text=text, messages=transcript, tool_results=collected)

            transcript.append(Message(role="assistant", content=text, tool_calls=calls))
            for call in calls:
                result = await self._execute(call, on_tool)
                collected.append(result)
                transcript.append(
                    Message(role="tool", content=result.for_model(), tool_call_id=call.id)
                )

        # Step budget exhausted: return whatever text we have.
        return AgentResult(text=text, messages=transcript, tool_results=collected)

    def _redeem_from_transcript(self, transcript: Sequence[Message]) -> None:
        """Redeem confirmation tokens found in the most recent user message."""
        for message in reversed(transcript):
            if message.role == "user":
                self.confirmations.redeem_from_message(message.content)
                return

    async def _execute(self, call: ToolCall, on_tool: ToolSink | None) -> ToolResult:
        try:
            tool = self.registry.get(call.name)
        except KeyError as exc:
            return ToolResult(content=str(exc), is_error=True)

        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(content=f"Invalid JSON arguments: {exc}", is_error=True)
        if not isinstance(arguments, dict):
            return ToolResult(content="Tool arguments must be a JSON object", is_error=True)

        refusal = self.scope.check(call.name, tool.tier)
        if refusal is not None:
            return ToolResult(content=refusal, is_error=True)

        if tool.tier is Tier.IRREVERSIBLE and not self.confirmations.consume(call.name, arguments):
            pending = self.confirmations.request(call.name, arguments)
            return ToolResult(
                content=_confirmation_message(pending.token, call.name, arguments),
                is_error=True,
                metadata={"confirmation_token": pending.token, "requires_confirmation": True},
            )

        if on_tool:
            on_tool(call.name, arguments)

        try:
            result = await tool.run(**arguments)
        except Exception as exc:  # surface tool failures to the model instead of crashing
            return ToolResult(content=f"Tool {call.name!r} failed: {exc}", is_error=True)

        self.scope.charge_result(result)
        return result
