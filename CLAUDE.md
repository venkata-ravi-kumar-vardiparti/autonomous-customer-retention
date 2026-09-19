# ChurnGuard

Multi-agent retention decision-support prototype for a telecom contact
centre. This file is the persistent context for every phase — read it before
touching `contracts/`, and update it when a phase changes something a later
phase needs to know.

## Business context

A customer calls to cancel. In under 2 seconds, the system must read the
live transcript, pull the account and billing picture, check competitor
pricing, apply eligibility rules deterministically, and hand the human agent
a ranked set of retention offers with rationale, confidence and cited
evidence.

**The system RECOMMENDS. A human APPROVES. A separate service EXECUTES.**
ChurnGuard never mutates a customer's account directly — `contracts/approval.py`
is the seam where its responsibility ends.

## Stack (fixed)

Python 3.12, OpenAI Agents SDK (`openai-agents`), Pydantic v2, SQLite via
`aiosqlite`, pytest + pytest-asyncio, FastAPI + Streamlit for the UI later.

## Architecture (fixed — do not redesign)

Five agents, orchestrated by a Supervisor using **agents-as-tools** (not
handoffs):

- **Conversation agent** — transcript → intents, churn signals, concerns
- **Customer 360 agent** — account, billing, usage, device financing
- **Competitor agent** — curated timestamped price snapshots, no scraping
- **Offer policy agent** — DETERMINISTIC rules in code; LLM only renders text
- **Supervisor agent** — fan-out, reconcile, score confidence, rank

All five read from a **Governed Data Layer**: read-only, masked,
provenance-stamped. Nothing in this codebase writes to a customer account.

## Repo layout

```
pyproject.toml  Makefile  .env.example  CLAUDE.md
src/churnguard/
  config.py                  process settings from env vars
  contracts/                 Pydantic contracts — source of truth, see below
  data/                      Governed Data Layer — see "Governed Data Layer" below
  policy/                    deterministic eligibility rules (empty — Phase 5+)
  telemetry/                 cost/latency/token emitters (empty)
  guardrails/                prompt-injection / safety guardrails (empty)
  tools/                     Agents SDK tool wrappers over data/ (empty)
  agents/                    the five agents as agents-as-tools (empty)
  offers/                    candidate offer generation (empty)
  orchestration/             Supervisor fan-out/reconcile/rank (empty)
  approval/                  human approval workflow (empty)
  execution/                 boundary to the (out-of-scope) execution service
  api/                       FastAPI surface (empty)
ui/                          Streamlit UI (empty)
fixtures/transcripts/        12 fixture transcripts, see below
experiments/                 scratch space for offline experiments
scripts/export_schemas.py    `make schemas` entry point
schemas/                     committed JSON Schema snapshots (drift-tested)
tests/                       pytest suite, mirrors src/ layout
.github/workflows/ci.yml     lint + typecheck + test
```

Note the deliberate name collisions: `contracts/offers.py` (Pydantic
payloads) vs. `offers/` (candidate-generation logic); `contracts/approval.py`
vs. `approval/` (workflow logic). Don't conflate them.

## Contracts — source of truth for all later phases

Everything under `src/churnguard/contracts/` is load-bearing for phases 1–11.
Changing a contract field's type or removing a field is a cross-phase
breaking change — think before doing it, and regenerate + review
`schemas/` (`make schemas`) whenever you do.

| Module | Payloads |
|---|---|
| `envelope.py` | `AgentEnvelope[T]`, `AgentResult[T]` — generic transport wrappers (PEP 695 syntax, `class Foo[T](BaseModel)`), `EvidenceRef`, `Telemetry` |
| `conversation.py` | `ConversationInput`, `ConversationSignals`, `TranscriptTurn`, `Intent`, `ChurnSignal`, `CompetitorClaim`, `StatedFigure`, `Concern` |
| `customer.py` | `CustomerContextRequest`, `AccountContext`, `Billing`, `DeltaCause`, `PaymentHistorySummary`, `DeviceFinancingLine`, `PlanProfile`, `VerificationResult` |
| `competitor.py` | `CompetitorQuery`, `CompetitorComparison`, `ResolvedCompetitorOffer`, `Normalization`, `SwitchingCosts`, `ClaimReconciliation`, `SnapshotMeta` |
| `offers.py` | `CandidateOffer`, `OfferComponent` (pre-policy, unevaluated) |
| `policy.py` | `PolicyEvaluationRequest`, `Disclosure`, `Verdict`, `ComputedLimits`, `PolicyVerdictSet` |
| `recommendation.py` | `SupervisorInput`, `Recommendation`, `CustomerImpact`, `ConfidenceAdjustment`, `BlockedCandidate`, `ApprovalRequirements`, `TraceContext`, `RecommendationSet` |
| `approval.py` | `ApprovalDecision`, `OfferEdit`, `ExecutionRequest`, `ExecutionOperation` |

## Non-negotiable rules

- **Every** `BaseModel` sets `model_config = ConfigDict(extra="forbid")`.
  Enforced by `tests/contracts/test_extra_forbid.py`, which walks
  `contracts/` reflectively and fails on any omission. The Agents SDK's
  `output_type` uses strict JSON schema; a permissive model would silently
  swallow hallucinated fields.
- `AgentEnvelope`/`AgentResult` are true generics and pass `mypy --strict`
  (PEP 695 type-parameter syntax, no `TypeVar`/`Generic` import needed on
  3.12).
- `RecommendationSet.commitment_status` is `Literal["none"]` — it cannot
  hold any other value by construction. ChurnGuard never represents itself
  as having committed to anything.
- All identifiers in examples/fixtures are masked: `ACCT_****4471`,
  `AGT_****0192`, `CALL_****1001`, etc. Never put a real-looking full
  identifier in test data or docs.
- The Offer Policy agent's *evaluation* is deterministic code
  (`evaluation_mode: Literal["deterministic"]` on `PolicyVerdictSet`). An
  LLM only renders the disclosure/rationale text around a verdict already
  computed — it never decides the verdict itself.
- ChurnGuard recommends; it does not execute. `ExecutionRequest` is a
  hand-off payload to a separate, out-of-scope service, gated behind a
  human `ApprovalDecision`.

## Assumptions made in Phase 0 — confirm before relying on them in later phases

These fields were structurally unspecified in the original brief. Shapes
below reflect decisions confirmed with the user during Phase 0 scaffolding;
flag it if a later phase's real requirements don't fit:

- **`CandidateOffer.type`** is a closed `Literal` (`bill_credit`,
  `plan_discount`, `device_credit`, `plan_change`, `retention_bundle`,
  `contract_buyout`) — chosen over a free string so the deterministic policy
  engine can pattern-match safely. Adding a new offer type is a contract
  change.
- **`AccountContext.device_financing`** is `list[DeviceFinancingLine]` with
  `line_ref, device, remaining_balance, monthly_payment, months_remaining,
  early_termination_fee`.
- **`RecommendationSet.approval`** is `ApprovalRequirements` (what a human
  must do — min tier, second-approver flag, pending disclosures), *not* the
  actual decision — the set is generated before a human has approved
  anything. The real decision is `contracts/approval.py::ApprovalDecision`,
  linked back by `recommendation_set_id`.
- **`RecommendationSet.trace`** is `TraceContext` (trace_id, span_id,
  ordered list of agent calls) — an observability echo of the
  `AgentEnvelope` tracing fields, for audit/debugging, not a new tracing
  system.
- **`CompetitorComparison`** sub-fields (`normalization`, `switching_costs`,
  `claim_reconciliation`) and **`PolicyVerdictSet.computed_limits`** and
  **`Recommendation.customer_impact`** and **`AccountContext.payment_history`**
  were each given a small typed sub-model rather than a raw string/dict —
  see the module docstrings in `contracts/*.py` for the exact fields chosen.
  `PolicyEvaluationRequest.account_digest` was kept as `dict[str, Any]` per
  the literal spec ("account_digest: dict") since it's consumed by
  deterministic code, not by an LLM structured output.
- `Verdict.constraint_violations`, `ApprovalDecision.edits` (→ `OfferEdit`),
  `ExecutionRequest.operations` (→ `ExecutionOperation`) were similarly
  given minimal concrete shapes; revisit when Phase 5 (policy) and Phase 8/9
  (approval/execution) define their real rule/operation vocab.

## Governed Data Layer (`data/`, built Phase 1)

The single read-only, masked, provenance-stamped source for account data.
Nothing outside `data/` ever touches SQLite directly.

- **`db.py`** — connection factories. `get_readonly_connection()` opens
  `file:<path>?mode=ro`; SQLite enforces the read-only guarantee at the
  VFS level (a write raises `OperationalError`), not by convention.
  `get_writable_connection_for_migrations()` exists only for
  `seed/generate.py` and is deliberately **not** re-exported from
  `data/__init__.py`'s `__all__`. `run_query()` centralizes the
  `DB_LATENCY_MS` (default 40) artificial per-query `asyncio.sleep`.
- **`schema.sql`** — modelled on a "legacy billing graph": most
  cross-entity relationships (account→line, line→device financing,
  account→promo eligibility, account→note, account→billing event) are rows
  in `account_edges(from_node, edge_type, to_node)`, not plain foreign
  keys. `usage_daily` is the one exception (direct `line_id` column —
  high-cardinality time series, not a relationship). Protected-characteristic-adjacent
  columns (`date_of_birth`, `zip_plus4`, `marketing_segment`) and raw PII
  (`holder_full_name`, `autopay_card_pan`) live on `accounts` but no
  repository query ever selects them.
- **`masking.py`** — idempotent tokenisation: `mask_account_number`,
  `mask_pan`, `mask_name`, `mask_line_ref`. Each checks whether its input
  is already in masked form and returns it unchanged if so.
- **`provenance.py`** — `RepoResult[T]` (see "Provenance shape" below) and
  `make_evidence()`, which stamps an `EvidenceRef` with `source_system`,
  `as_of` and a `freshness` derived via `freshness.classify_freshness`.
- **`freshness.py`** — `age_days() <= 29` → `"fresh"`, `<= 40` → `"aging"`,
  else `"stale"`.
- **`seed/generate.py`** — deterministic synthetic data: fixed RNG seed
  (`SEED = 1337`) and a fixed reference clock (`SEED_REFERENCE_NOW =
  2026-09-19`, not wall-clock time) so reseeding is reproducible —
  verified by `compute_content_hash()`, which hashes row *content* in a
  fixed table/row order rather than the DB file's raw bytes (SQLite's own
  storage layer isn't byte-deterministic across writes even with identical
  data). 30 accounts total; the first 6 are the named scenarios from the
  phase brief (worked example `ACCT_****4471`, delinquent, no-financing,
  three-active-promos, contradictory-billing, churned) at fixed positions
  `0..5` in `build_all_accounts()`'s return list — tests locate them by
  rebuilding that same list from the same seed, not by hardcoding a masked
  ref. Last-4 digit `0000` is reserved and never assigned, giving tests a
  guaranteed-unused ref for the "not found" path.
- **`repositories/`** — `customer_repo`, `billing_repo`, `catalog_repo`,
  `promo_repo`, `competitor_repo`, `notes_repo`. See "Provenance shape" and
  "Repo input shape" below for the two governing design decisions.
  `customer_repo.get_account_context()` composes the others rather than
  querying tables itself, resolving `account_ref` → raw `account_id`
  exactly once per call via private `_get_x_by_id` variants.

### Provenance shape (confirmed with the user during Phase 1)

Most contracts (`Billing`, `PlanProfile`, `PaymentHistorySummary`,
`DeviceFinancingLine`, `AccountContext`) have no `source_system`/`as_of`
fields — only `EvidenceRef` (`contracts/envelope.py`) does, and only
`DeltaCause` has an `evidence_id` hook. Contracts are frozen, so every
repository method returns `RepoResult[T]` (`data/provenance.py`), pairing
the untouched contract payload with a `list[EvidenceRef]`. This is exactly
the shape `AgentResult.evidence` expects, so it slots in directly once
Phase 3 wraps repo calls in an `AgentResult`. Exception: `SnapshotMeta`
(competitor.py) is already a self-describing provenance record, so
`competitor_repo.get_snapshot_meta()` returns it bare, no `RepoResult`
wrapper.

### Repo input shape (confirmed with the user during Phase 1)

Every public repository function takes the **masked** `account_ref` (e.g.
`"ACCT_****4471"`, matching `CustomerContextRequest.account_ref`) and
resolves it internally to the raw `account_id` via a last-4-digit lookup
(`repositories/_base.py::resolve_account_id`). Raw internal identifiers
never cross the repository boundary in either direction. A masked
`account_ref` that doesn't match exactly one account raises `LookupError`;
a string that isn't shaped like a masked ref raises `ValueError`.

**Leak-safety note for future phases:** when adding a new evidence
`record_ref` or any other field that embeds an identifier, mask it first
(`mask_account_number`, etc.) — Phase 1 caught and fixed three instances
where a raw `account_id` had been embedded directly in an `EvidenceRef.record_ref`
(`customer_repo`, `billing_repo.get_payment_history`,
`catalog_repo.get_usage_by_line`) before `test_pii_leak.py` would have
caught it in CI. Also round monetary/derived floats
(`round(x, 2)`) before returning them — an unrounded float division/subtraction
(e.g. `140.0 / 3`) can produce a long decimal expansion that a PII-style
digit-run regex will flag as a false positive, on top of just being an
odd value to hand an agent.

## Draft phase plan (proposed, not yet confirmed — check with the user before treating as fixed)

0. **Done.** Scaffold + full contract set + fixtures + CI.
1. **Done.** Governed Data Layer (`data/`) — see above.
2. Conversation agent + `guardrails/` (prompt-injection resistance — see
   fixtures 03/11).
3. Customer 360 agent + `tools/` wrapping the data layer.
4. Competitor agent + curated snapshot store (no live scraping).
5. Offer Policy engine (`policy/`): deterministic rules, policy pack
   versioning/hashing.
6. Supervisor orchestration (`orchestration/`): fan-out, reconcile,
   `bounded_pipeline` mode first.
7. Confidence scoring, evidence citation, `telemetry/` emitters wired to
   `Telemetry`.
8. Approval workflow (`approval/`): `ApprovalDecision` persistence, edit
   tracking.
9. Execution boundary (`execution/`): `ExecutionRequest` validation against
   a mocked downstream service (still out of scope to actually execute).
10. FastAPI surface (`api/`) for the agent-facing UI.
11. Streamlit UI (`ui/`) + end-to-end demo, `experiments/` for offline
    tuning.

## Fixture transcripts (`fixtures/transcripts/`)

12 fixtures, each a JSON object directly parseable as
`ConversationInput(**{**raw, "prior_signals": None})`:

01 single intent (cancel) · 02 multi-intent (cancel + billing dispute) ·
03 prompt-injection attempt · 04 no competitor mention · 05 garbled audio ·
06 happy customer, no grievance · 07 multi-intent (cancel + device payoff) ·
08 specific competitor price claim · 09 vague/ambiguous-unit competitor
claim · 10 full audio dropout · 11 prompt-injection via fake supervisor
override · 12 sentiment escalation then de-escalation.

`tests/contracts/test_fixtures.py` asserts there are exactly 12 and that
each parses.

## Commands

```
make install     # uv sync --extra dev
make test        # uv run pytest
make lint        # uv run ruff check src tests scripts
make typecheck   # uv run mypy --strict src/churnguard/contracts src/churnguard/data
make schemas     # regenerate schemas/*.json from contracts/
```

On Windows without `make` available, run the underlying `uv run ...`
commands directly (see Makefile).

## Phase 0 acceptance status

- `pytest` passes (33 tests): reflective `extra="forbid"` check, runtime
  rejection of an unknown field, round-trip (`model → json → model`,
  byte-identical `model_dump_json()`) for every top-level contract
  including both generics, schema-drift check against committed
  `schemas/`, and fixture parsing.
- `mypy --strict` passes on `src/churnguard/contracts`.
- `ruff check` passes on `src`, `tests`, `scripts`.
- Out of scope, as specified: no database, no agents, no policy logic, no
  UI — contracts and scaffold only.

## Phase 1 acceptance status

- `pytest` passes (72 tests, `tests/unit/`): read-only enforcement
  (`test_db_access.py`), the PII leak gate across every repo method × every
  seeded account plus competitor snapshots (`test_pii_leak.py`, zero hits),
  masking idempotence as a Hypothesis property test (`test_masking.py`),
  freshness boundaries at exactly 29/30/31 days (`test_freshness.py`),
  reseed content-hash determinism, and repository behaviour for every named
  scenario account (`test_repositories.py`).
- `mypy --strict` passes on `src/churnguard/contracts` **and**
  `src/churnguard/data` (Makefile/CI updated accordingly).
- `ruff check` passes on `src`, `tests`, `scripts`.
- Out of scope, as specified: no agents, no LLM calls, no policy rules, no
  orchestration.
