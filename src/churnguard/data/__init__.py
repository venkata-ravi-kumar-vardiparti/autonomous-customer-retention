"""Governed Data Layer: read-only, masked, provenance-stamped account/billing/usage access.

Module map:
    db.py             connection factories; only get_readonly_connection is
                       re-exported here — the writable factory
                       (get_writable_connection_for_migrations, in db.py)
                       is deliberately excluded from this package's __all__
    schema.sql         DDL for the "legacy billing graph" schema
    masking.py         idempotent tokenisation of account numbers, PANs, names
    provenance.py      RepoResult[T] + EvidenceRef stamping
    freshness.py       fresh / aging / stale classification
    seed/generate.py   deterministic synthetic seed data
    repositories/      customer_repo, billing_repo, catalog_repo, promo_repo,
                       competitor_repo, notes_repo — each returns
                       RepoResult[contract model], never raw rows
"""

from churnguard.data.db import get_readonly_connection, run_query

__all__ = ["get_readonly_connection", "run_query"]
