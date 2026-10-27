"""Tests for the tier ladder and scope of autonomy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from jpe.scope import ConfirmationBroker, ScopeOfAutonomy
from jpe.tiers import Tier


def test_tier_ordering() -> None:
    assert Tier.READ < Tier.REVERSIBLE < Tier.COSTLY < Tier.IRREVERSIBLE


def test_only_tier_three_needs_confirmation() -> None:
    assert not Tier.READ.needs_confirmation
    assert not Tier.REVERSIBLE.needs_confirmation
    assert not Tier.COSTLY.needs_confirmation
    assert Tier.IRREVERSIBLE.needs_confirmation


def test_scope_allows_glob_patterns() -> None:
    scope = ScopeOfAutonomy(allowed_tools=["calendar.*", "mail.read"])
    assert scope.allows_tool("calendar.list_events")
    assert scope.allows_tool("mail.read")
    assert not scope.allows_tool("mail.send")


def test_scope_empty_allowlist_allows_nothing() -> None:
    scope = ScopeOfAutonomy(allowed_tools=[])
    assert not scope.allows_tool("read_file")


def test_scope_expiry() -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    scope = ScopeOfAutonomy(latest_time=past)
    assert scope.is_expired()
    refusal = scope.check("read_file", Tier.READ)
    assert refusal is not None
    assert "expired" in refusal


def test_scope_without_deadline_never_expires() -> None:
    assert not ScopeOfAutonomy().is_expired()


def test_scope_refuses_tool_outside_allowlist() -> None:
    scope = ScopeOfAutonomy(allowed_tools=["read_file"])
    refusal = scope.check("write_file", Tier.REVERSIBLE)
    assert refusal is not None
    assert "outside the current scope" in refusal


def test_scope_allows_tool_inside_allowlist() -> None:
    scope = ScopeOfAutonomy(allowed_tools=["read_file"])
    assert scope.check("read_file", Tier.READ) is None


def test_scope_cost_budget() -> None:
    scope = ScopeOfAutonomy(max_cost=Decimal("1.00"))
    assert scope.can_afford(Decimal("0.60"))
    scope.charge(Decimal("0.60"))
    assert not scope.can_afford(Decimal("0.60"))
    assert scope.can_afford(Decimal("0.40"))


def test_scope_refuses_when_budget_exhausted() -> None:
    scope = ScopeOfAutonomy(max_cost=Decimal("1.00"))
    scope.charge(Decimal("1.00"))
    refusal = scope.check("read_file", Tier.READ)
    assert refusal is not None
    assert "budget is exhausted" in refusal


def test_scope_without_budget_never_refuses_on_cost() -> None:
    scope = ScopeOfAutonomy()
    scope.charge(Decimal("9999"))
    assert scope.check("read_file", Tier.READ) is None


def test_charge_result_reads_metadata_cost() -> None:
    from jpe.tools.base import ToolResult

    scope = ScopeOfAutonomy(max_cost=Decimal("1.00"))
    scope.charge_result(ToolResult(content="ok", metadata={"cost": "0.25"}))
    assert scope.spent == Decimal("0.25")
    scope.charge_result(ToolResult(content="ok", metadata={"cost": 0.5}))
    assert scope.spent == Decimal("0.75")


def test_charge_result_ignores_missing_or_bad_cost() -> None:
    from jpe.tools.base import ToolResult

    scope = ScopeOfAutonomy()
    scope.charge_result(ToolResult(content="ok"))
    scope.charge_result(ToolResult(content="ok", metadata={"cost": "not a number"}))
    assert scope.spent == Decimal("0")


def test_scope_constraint_lookup() -> None:
    scope = ScopeOfAutonomy(tool_constraints={"mail.send": {"requires_confirm": True}})
    assert scope.requires_confirmation("mail.send")
    assert not scope.requires_confirmation("mail.read")


def test_confirmation_round_trip() -> None:
    broker = ConfirmationBroker()
    pending = broker.request("mail.send", {"to": "a@example.com"})

    assert not broker.consume("mail.send", {"to": "a@example.com"})

    redeemed = broker.redeem_from_message(f"yes, go ahead {pending.token}")
    assert len(redeemed) == 1
    assert broker.consume("mail.send", {"to": "a@example.com"})


def test_confirmation_is_single_use() -> None:
    broker = ConfirmationBroker()
    pending = broker.request("mail.send", {"to": "a@example.com"})
    broker.redeem_from_message(pending.token)

    assert broker.consume("mail.send", {"to": "a@example.com"})
    assert not broker.consume("mail.send", {"to": "a@example.com"})


def test_confirmation_is_bound_to_arguments() -> None:
    """A yes for one call must not authorise a different call."""
    broker = ConfirmationBroker()
    pending = broker.request("mail.send", {"to": "a@example.com"})
    broker.redeem_from_message(pending.token)

    assert not broker.consume("mail.send", {"to": "attacker@example.com"})


def test_confirmation_token_is_case_insensitive_in_message() -> None:
    broker = ConfirmationBroker()
    pending = broker.request("mail.send", {"to": "a@example.com"})
    broker.redeem_from_message(pending.token.lower())
    assert broker.consume("mail.send", {"to": "a@example.com"})


def test_confirmation_expires() -> None:
    broker = ConfirmationBroker(ttl=timedelta(seconds=-1))
    pending = broker.request("mail.send", {"to": "a@example.com"})
    assert broker.redeem_from_message(pending.token) == []
    assert not broker.consume("mail.send", {"to": "a@example.com"})


def test_unrelated_message_redeems_nothing() -> None:
    broker = ConfirmationBroker()
    broker.request("mail.send", {"to": "a@example.com"})
    assert broker.redeem_from_message("what's the weather?") == []
