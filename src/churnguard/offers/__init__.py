"""Candidate offer generation, feeding the deterministic policy engine.

Not to be confused with churnguard.contracts.offers (the Pydantic payloads).

Module map (Phase 6):
    normalizer.py   Pure functions: like-for-like competitor price
                     normalization, switching costs, breakeven, claim
                     reconciliation, freshness/confidence penalty. No I/O,
                     no model call - see its module docstring.
    generator.py     generate_candidates(): deterministic, rule-based
                     CandidateOffer generation from AccountContext +
                     ConversationSignals + CompetitorComparison.
"""
