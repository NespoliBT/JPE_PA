"""Live scenarios: the tier-3 confirmation flow against a real model.

Skipped unless ``OPENROUTER_API_KEY`` is set. These assert on the *shape* of the
interaction — how many turns it takes, whether a token is issued, whether the
action runs — not on the prose.

The two-turn property is the one worth guarding: if the model asks for
permission in prose before calling the tool, the user's "yes" authorises
nothing and the flow costs an extra round-trip.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from jpe.agent import Agent
from jpe.config import get_settings
from jpe.llm.client import ChatClient
from jpe.llm.models import Message
from jpe.memory.store import reset_store
from jpe.soul import build_system_prompt
from jpe.tools.registry import discover

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY is not set",
    ),
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("JPE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("JPE_MEMORY_DIR", "memory")
    monkeypatch.setenv("JPE_SOUL_PATH", "SOUL.md")
    monkeypatch.setenv("JPE_POLICY_PATH", "POLICY.md")
    monkeypatch.setenv("JPE_ALLOW_SHELL", "true")
    get_settings.cache_clear()
    reset_store()
    yield tmp_path
    reset_store()
    get_settings.cache_clear()


def _agent() -> Agent:
    return Agent(ChatClient(), discover())


def _system() -> Message:
    return Message(role="system", content=build_system_prompt())


async def test_tier_three_issues_a_token_on_the_first_turn(workspace: Path) -> None:
    """The model must call the tool, not ask in prose, so the token arrives at once."""
    agent = _agent()
    result = await agent.run(
        [_system(), Message(role="user", content="Run the shell command: echo hello")]
    )

    pending = agent.confirmations.pending()
    assert pending, f"no token issued; model said: {result.text!r}"
    assert pending[0].tool_name == "run_command"
    assert pending[0].token in result.text.upper(), "token was not shown to the user"


async def test_tier_three_runs_when_the_token_comes_back(workspace: Path) -> None:
    """Two turns: request, then the token. The command must actually run."""
    agent = _agent()
    history = [_system(), Message(role="user", content="Run the shell command: echo hello")]

    first = await agent.run(history)
    history = first.messages
    pending = agent.confirmations.pending()
    assert pending, f"no token issued; model said: {first.text!r}"

    history.append(Message(role="user", content=pending[0].token))
    second = await agent.run(history)

    assert any("hello" in r.content for r in second.tool_results), (
        f"command did not run; model said: {second.text!r}"
    )


async def test_tier_three_does_not_run_without_a_token(workspace: Path) -> None:
    """A plain 'yes' must not authorise anything."""
    agent = _agent()
    history = [_system(), Message(role="user", content="Run the shell command: echo hello")]

    first = await agent.run(history)
    history = first.messages
    history.append(Message(role="user", content="yes, go ahead"))

    second = await agent.run(history)

    assert not any("hello" in r.content for r in second.tool_results), (
        "a plain yes executed a tier-3 action"
    )
    assert agent.confirmations.pending(), "the token should still be pending"
