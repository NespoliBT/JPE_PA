"""Scoped autonomy and tier-3 confirmation.

Autonomy is bounded by a declared scope, not by a prompt. A scope says which
tools may run, until when, at what cost, and with what per-tool constraints.
Every scope has an ending condition; a scope without one silently authorises
the next task too.

Tier-3 tools additionally require an explicit yes. Because approval prompts were
removed deliberately, the yes is *model-mediated*: the tool refuses, the agent
loop issues a single-use token, the model asks the user in prose, and the user's
next message must contain that token. No terminal prompt, no blanket approval.

See ``research-v2.md`` §3.1 and §3.2.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from jpe.tiers import Tier
from jpe.tools.base import ToolResult

#: How long a confirmation token stays valid.
DEFAULT_CONFIRMATION_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(UTC)


def _fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """Stable identity for a (tool, arguments) pair.

    A token authorises exactly one call with exactly these arguments, so a
    confirmed ``delete_event(id=1)`` cannot be replayed as ``delete_event(id=2)``.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{tool_name}\x00{canonical}".encode()).hexdigest()
    return digest[:16]


@dataclass(slots=True)
class ScopeOfAutonomy:
    """The bounds within which the assistant may act without asking.

    ``allowed_tools`` holds glob patterns, so ``calendar.*`` and ``mail.read``
    both work. An empty list means "no tools", not "all tools".
    """

    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    latest_time: datetime | None = None
    max_cost: Decimal | None = None
    tool_constraints: dict[str, Any] = field(default_factory=dict)
    additional_details: str = ""

    #: Running total of costs reported by tools, checked against ``max_cost``.
    spent: Decimal = Decimal("0")

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether the scope's ending condition has passed."""
        if self.latest_time is None:
            return False
        return (now or _now()) >= self.latest_time

    def allows_tool(self, name: str) -> bool:
        """Whether ``name`` matches any allowed pattern."""
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.allowed_tools)

    def constraint(self, name: str) -> dict[str, Any]:
        """Return the per-tool constraint block, if any."""
        value = self.tool_constraints.get(name)
        return value if isinstance(value, dict) else {}

    def requires_confirmation(self, name: str) -> bool:
        """Whether this tool needs an explicit yes under this scope."""
        return bool(self.constraint(name).get("requires_confirm"))

    def can_afford(self, cost: Decimal) -> bool:
        """Whether spending ``cost`` stays within ``max_cost``."""
        if self.max_cost is None:
            return True
        return self.spent + cost <= self.max_cost

    def charge(self, cost: Decimal) -> None:
        """Record spend against the scope."""
        self.spent += cost

    def charge_result(self, result: ToolResult) -> None:
        """Charge whatever cost a tool reported in ``result.metadata``.

        Tools that spend money (or tokens) report ``metadata["cost"]`` as a
        number or a decimal string. Anything unparseable is ignored rather than
        crashing the loop.
        """
        raw = result.metadata.get("cost")
        if raw is None:
            return
        try:
            self.charge(Decimal(str(raw)))
        except (ArithmeticError, ValueError):
            return

    def check(self, name: str, tier: Tier, now: datetime | None = None) -> str | None:
        """Return a refusal reason, or ``None`` if the call may proceed.

        This is the single gate the agent loop consults. It does not handle
        tier-3 confirmation — that is :class:`ConfirmationBroker`'s job, because
        confirmation is stateful across turns.

        ``tier`` is accepted for the future POLICY.md-derived tier overrides
        (Phase 2, when MCP tools arrive without a declared tier). Today every
        tool declares its own tier, so the gate only consults the allowlist,
        the deadline, and the budget.
        """
        if self.is_expired(now):
            return (
                "The scope of autonomy expired at "
                f"{self.latest_time.isoformat() if self.latest_time else 'unknown'}. "
                "Ask the user to grant a new scope."
            )
        if not self.allows_tool(name):
            return (
                f"Tool {name!r} is outside the current scope of autonomy "
                f"(allowed: {', '.join(self.allowed_tools) or 'none'}). "
                "Ask the user to widen the scope if this is intended."
            )
        if self.max_cost is not None and self.spent >= self.max_cost:
            return (
                f"The scope's budget is exhausted ({self.spent} of {self.max_cost} spent). "
                "Ask the user to grant a new scope."
            )
        return None


@dataclass(slots=True)
class PendingConfirmation:
    """A tier-3 call waiting for the user's yes."""

    token: str
    tool_name: str
    fingerprint: str
    arguments: dict[str, Any]
    created_at: datetime
    expires_at: datetime

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return self.tool_name == tool_name and self.fingerprint == _fingerprint(
            tool_name, arguments
        )


class ConfirmationBroker:
    """Issues and redeems single-use confirmation tokens.

    The flow, end to end:

    1. The model calls a tier-3 tool.
    2. The loop finds no valid token and calls :meth:`request`, which returns a
       token and a message telling the model to ask the user.
    3. The model asks in prose, quoting the token.
    4. The user replies with the token in their next message.
    5. :meth:`redeem_from_message` scans that message, marks the token
       authorised, and the loop retries the call.

    Tokens are single-use, bound to the exact arguments, and expire.
    """

    def __init__(self, ttl: timedelta = DEFAULT_CONFIRMATION_TTL) -> None:
        self.ttl = ttl
        self._pending: dict[str, PendingConfirmation] = {}
        self._authorised: dict[str, PendingConfirmation] = {}

    def request(self, tool_name: str, arguments: dict[str, Any]) -> PendingConfirmation:
        """Create a pending confirmation and return it."""
        self._prune()
        token = secrets.token_hex(3).upper()
        now = _now()
        pending = PendingConfirmation(
            token=token,
            tool_name=tool_name,
            fingerprint=_fingerprint(tool_name, arguments),
            arguments=dict(arguments),
            created_at=now,
            expires_at=now + self.ttl,
        )
        self._pending[token] = pending
        return pending

    def redeem_from_message(self, message: str) -> list[PendingConfirmation]:
        """Authorise any pending tokens that appear in ``message``.

        Returns the confirmations that were redeemed. A token is only honoured
        if it is still pending and unexpired.
        """
        self._prune()
        redeemed: list[PendingConfirmation] = []
        upper = message.upper()
        for token, pending in list(self._pending.items()):
            if token in upper:
                del self._pending[token]
                self._authorised[token] = pending
                redeemed.append(pending)
        return redeemed

    def consume(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Redeem an authorisation for this exact call, if one exists.

        Consuming removes it, so the same yes cannot authorise a second call.
        """
        self._prune()
        for token, pending in list(self._authorised.items()):
            if pending.matches(tool_name, arguments):
                del self._authorised[token]
                return True
        return False

    def pending(self) -> list[PendingConfirmation]:
        """Return unexpired pending confirmations."""
        self._prune()
        return list(self._pending.values())

    def _prune(self) -> None:
        now = _now()
        for store in (self._pending, self._authorised):
            for token in [t for t, p in store.items() if p.expires_at <= now]:
                del store[token]
