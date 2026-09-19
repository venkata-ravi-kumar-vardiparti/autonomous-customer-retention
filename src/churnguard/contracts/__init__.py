"""Pydantic contract set for ChurnGuard.

Every model in this package sets ``model_config = ConfigDict(extra="forbid")``.
The Agents SDK renders these as strict JSON schemas for structured outputs; a
permissive model would silently swallow hallucinated fields instead of
rejecting them. See tests/contracts/test_extra_forbid.py for the enforcing
test.

Module map:
    envelope.py        AgentEnvelope[T] / AgentResult[T] transport wrappers
    conversation.py     Conversation agent payloads
    customer.py         Customer 360 agent payloads
    competitor.py       Competitor agent payloads
    offers.py           CandidateOffer / OfferComponent (pre-policy)
    policy.py           Offer Policy agent payloads (deterministic engine)
    recommendation.py   Supervisor agent payloads
    approval.py         Human approval + execution handoff payloads
"""
