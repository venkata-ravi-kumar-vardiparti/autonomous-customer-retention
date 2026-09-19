"""Individual rule evaluators. Each returns None when it doesn't apply.

A rule module never sees the transcript, the customer, or an LLM — only an
OfferComponent (or CandidateOffer, for the one universal check), the parsed
AccountDigest, the requesting agent's tier, and the loaded PolicyPack.
"""

from __future__ import annotations

from dataclasses import dataclass

from churnguard.contracts.policy import Disclosure


@dataclass(frozen=True)
class RuleOutcome:
    """What one rule decided about one component (or, for RET-002, one candidate).

    blocked=True is the only thing that can ever push a verdict to
    "blocked" — see policy/engine.py::_combine_verdict, which checks it
    unconditionally and first.
    """

    rule_id: str
    blocked: bool
    tier_required: int | None
    violation_reason: str | None
    disclosure: Disclosure | None = None


__all__ = ["RuleOutcome"]
