"""Tool tiers: the reversibility ladder.

``mutating: bool`` was a two-value approximation of a four-value ladder. A tool
that writes a git-tracked file and a tool that sends mail are both "mutating",
but only one of them can be undone.

See ``research-v2.md`` §3.2.
"""

from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    """How reversible a tool's side effects are."""

    #: Read-only. Always allowed.
    READ = 0
    #: Reversible by the assistant itself (git-tracked writes, memory).
    REVERSIBLE = 1
    #: Reversible by the user with effort (drafts, calendar creates).
    COSTLY = 2
    #: Irreversible or externally visible (send mail, delete, spend money).
    IRREVERSIBLE = 3

    @property
    def needs_confirmation(self) -> bool:
        """Whether this tier requires an explicit yes before running."""
        return self is Tier.IRREVERSIBLE

    @property
    def label(self) -> str:
        return {
            Tier.READ: "read",
            Tier.REVERSIBLE: "reversible",
            Tier.COSTLY: "costly",
            Tier.IRREVERSIBLE: "irreversible",
        }[self]
