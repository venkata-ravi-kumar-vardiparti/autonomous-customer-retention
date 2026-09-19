"""Deterministic offer-eligibility rules. No LLM call anywhere in this package.

Module map:
    packs/v2026.09.1.yaml   the versioned policy pack (limits, rules,
                            prohibited actions)
    loader.py               load + SHA-256 hash a pack; unknown rule IDs
                             raise at load time
    digest.py               validate PolicyEvaluationRequest.account_digest
                             into a typed AccountDigest — never silently
                             defaults a missing/malformed field
    engine.py                evaluate()/evaluate_with_pack(): pure functions,
                             no I/O in the evaluation path itself
    rules/                  retention.py (RET-014, RET-002), billing.py
                            (BIL-003), plan.py (PLN-021), financing.py
                            (FIN-009), prohibited.py (PRO-007)

Independent of data/ — this package only ever consumes an account_digest
dict, never a repository. Do not import from churnguard.data here.
"""
