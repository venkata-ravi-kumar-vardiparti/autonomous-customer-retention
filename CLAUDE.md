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
  data/                      Governed Data Layer (empty — Phase 1+)
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

## Draft phase plan (proposed, not yet confirmed — check with the user before treating as fixed)

0. **This phase.** Scaffold + full contract set + fixtures + CI.
1. Governed Data Layer (`data/`): SQLite schema, read-only/masked access,
   provenance stamping, seeded from `fixtures/`.
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
make typecheck   # uv run mypy --strict src/churnguard/contracts
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
