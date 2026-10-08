"""The order of authority (spec §10.2), enforced in code.

confirmed by the person > rule > merchant confirmed by research > inferred memory > LLM.
A specialist may replace an understanding only when its authority is at least the row's
current authority, and never when the person has confirmed the row. The database trigger
`understanding_confirmed_is_the_persons` refuses the same thing a second time.
"""

from __future__ import annotations

from typing import Final

from tuppence.knowledge.models import DecidedBy, Understanding

HUMAN: Final = 100  # the person, directly
USER_RULE: Final = 80  # a rule the person made or accepted
CODE_RULE: Final = 70  # rules Tuppence ships, and transfer pairing
CONFIRMED_MEMORY: Final = 60  # a merchant confirmed by research (M5) or by the person
INFERRED_MEMORY: Final = 40  # a merchant's usual category, worked out from earlier decisions
MODEL: Final = 20  # the AI model (categorise and review)


def authority_for(
    decided_by: DecidedBy, *, seed_rule: bool = False, confirmed: bool = False
) -> int:
    if decided_by == "human":
        return HUMAN
    if decided_by == "rule":
        return CODE_RULE if seed_rule else USER_RULE
    if decided_by == "research":
        return CONFIRMED_MEMORY
    if decided_by == "memory":
        return CONFIRMED_MEMORY if confirmed else INFERRED_MEMORY
    return MODEL


def may_replace(current: Understanding, decided_by: DecidedBy, authority: int) -> bool:
    """True when a decision with this authority may overwrite `current`."""
    if decided_by == "human":
        return True
    if current.status == "confirmed":
        return False
    return authority >= current.authority
