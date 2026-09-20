"""Candidate offer generation, feeding the deterministic policy engine.

Not to be confused with churnguard.contracts.offers (the Pydantic payloads).

Module map:
    normalizer.py   (Phase 6) Pure functions: like-for-like competitor price
                     normalization, switching costs, breakeven, claim
                     reconciliation, freshness/confidence penalty. No I/O,
                     no model call - see its module docstring.
    generator.py     (Phase 6) generate_candidates(): deterministic,
                     rule-based CandidateOffer generation from AccountContext
                     + ConversationSignals + CompetitorComparison.
    ranker.py        (Phase 7) rank_candidates(): orders already
                     policy-cleared candidates by retention likelihood x
                     margin, never by discount size alone.
"""
