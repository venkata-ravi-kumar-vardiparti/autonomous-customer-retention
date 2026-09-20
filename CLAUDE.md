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
  policy/                    deterministic eligibility rules — see "Offer Policy engine" below
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

## Offer Policy engine (`policy/`, built Phase 2)

The compliance boundary: offer eligibility verdicts produced by code,
versioned, reproducible, zero model involvement. Independent of `data/` —
consumes `account_digest: dict[str, Any]` (from `PolicyEvaluationRequest`,
frozen contract), never a repository, and does not import `churnguard.data`
anywhere (checked by `test_policy_engine.py::test_engine_module_has_no_llm_or_data_imports`,
which parses the module source with `ast` rather than trusting a grep).

- **`packs/v2026.09.1.yaml`** — the versioned pack: `limits`
  (`max_monthly_discount_tier1/2`, `max_discount_pct`,
  `max_bundle_duration_months`), `authority_tiers` (name → int, e.g.
  `regional_manager: 3`), `rules` (RET-014, RET-002, BIL-003, PLN-021,
  FIN-009) and `prohibited_actions` (PRO-007).
- **`loader.py`** — `load_pack_from_dict/_yaml_text/_path`, each validating
  the pack's declared rule IDs against `KNOWN_RULE_IDS`/`KNOWN_PROHIBITED_IDS`
  **in both directions** (pack declares an ID with no code, or code
  implements an ID the pack never declares — both raise `PolicyPackError`
  at load time). `compute_pack_hash()` is a SHA-256 of the canonical
  (sorted-key) JSON of the whole pack dict, not of the YAML file's raw
  bytes — so a mutation test can flip an in-memory dict and re-hash without
  touching disk. `get_pack(version)` is `@cache`d: the only I/O in the
  whole package, and it happens once per version, before evaluation.
- **`digest.py`** — `parse_account_digest()` turns the loose
  `account_digest` dict into a typed `AccountDigest` (`current_monthly`,
  `active_promo_codes`, `financing_active`, `payment_current`). Every
  required key must be present and correctly typed or it raises
  `PolicyDigestError` — nothing is ever silently defaulted, including a
  `current_monthly <= 0`.
- **`engine.py`** — `evaluate(request, *, now=None)` is the public entry
  point matching the phase brief's literal signature; it resolves the pack
  via `loader.get_pack` and delegates to `evaluate_with_pack(request, pack,
  *, now=None)`, the true pure core (no I/O, total function of its
  inputs — see "why `now` is a parameter" below). `_combine_verdict()` is
  the **one** place a `Verdict.verdict` value is decided, and it checks
  `blocked` unconditionally and first — no other code path constructs a
  verdict, so a blocked outcome can't structurally be promoted to `pass`.
- **`rules/`** — each rule module exposes a function returning
  `RuleOutcome | None` (`rules/__init__.py`). `None` means "doesn't apply
  to this component." Two rule *kinds*:
  - **Category rules** (RET-014, BIL-003, PLN-021, FIN-009, PRO-007): keyed
    to a component-code prefix (`RET_`, `BIL_`, `PLN_`, `FIN_`, `PRC_` —
    see "Offer component convention" below). Appear in `governing_rules`
    whenever their category is present, pass or block.
  - **The one universal rule** (RET-002, discount % of `current_monthly`):
    applies to every candidate regardless of category, but only ever
    returns an outcome — and so only ever appears in `governing_rules` —
    when it's actually violated. A passing check contributes nothing.

### Why `now` is a keyword parameter, not a contract field

`PolicyVerdictSet.evaluated_at` needs a timestamp, but a function that
reads the wall clock internally isn't pure and can't produce byte-identical
output across repeated calls (acceptance criterion 1: 1000 iterations,
identical input, identical output). `evaluate()`/`evaluate_with_pack()`
both take `now: datetime | None = None`; real callers can omit it (falls
back to `datetime.now(UTC)`), and every determinism/mutation test passes a
fixed value explicitly. Same pattern as `data/provenance.py::make_evidence`'s
`now` parameter in Phase 1 — deliberate precedent, not a coincidence.

### Offer component convention (decided in Phase 2, no prior contract guidance)

`CandidateOffer.type` (frozen, `contracts/offers.py`) is a coarse,
whole-candidate enum (`bill_credit`, `plan_change`, `retention_bundle`,
etc.) and can't express that one candidate bundles two different rule
categories (e.g. the reference C1 is a retention credit *and* an autopay
restoration in one `bill_credit` candidate). Category detection is
therefore done per-`OfferComponent` via a **code prefix convention**, not
`CandidateOffer.type`:

| Prefix | Category | Rule |
|---|---|---|
| `RET_` | retention credit | RET-014 |
| `BIL_` | autopay discount restoration | BIL-003 |
| `PLN_` | plan migration (hotspot reduction) | PLN-021 |
| `FIN_` | device-financing credit | FIN-009 |
| `PRC_` | competitor price match | PRO-007 |

**Any future phase that generates `CandidateOffer`s (offer generation,
Supervisor) must use these prefixes on `OfferComponent.code`** for the
policy engine to route them correctly — an unrecognized prefix simply
passes through with no governing rules, silently, not an error. This is
the one place a genuinely wrong guess would be expensive to unwind, and
it's a decision made in-package (not asked of the user) because it doesn't
touch a frozen contract.

## Telemetry spine (`telemetry/`, built Phase 3)

Instrumentation built before the first agent exists, so nothing is ever
retrofitted. Independent of `data/` and `policy/` — imports only
`contracts/` and `config.py`.

- **`tracer.py`** — `AgentSpan`, an async context manager: one per agent
  call. Records `model`, `latency_ms`, `tokens_in`/`tokens_out` (via
  `record_usage()`), `cost_usd` (via `cost.compute_cost_usd`) and
  `cache_hit` into a `contracts.envelope.Telemetry` on exit; never swallows
  an exception (`__aexit__` always returns `False`), recording `status:
  "error"` first. `trace_id`/`span_id`/`parent_span_id` are **explicit**
  constructor args, not inferred from the OpenAI Agents SDK's ambient
  "current span" contextvar — agents-as-tools fan out concurrently, and an
  `AgentEnvelope` can in principle carry its tracing fields across a
  process boundary, so parent/child linkage has to survive independent of
  call-stack nesting order. The SDK's own `Trace`/`Span` objects are still
  created and explicitly parented (by looking up the SDK span object for
  the given `parent_span_id`, not by trusting ambient context), so the
  "layered over the OpenAI Agents SDK tracing" integration is real, not
  decorative — but `export_trace(trace_id)` builds its nested tree from
  this module's own process-local `SpanRecord` store, never from the SDK's
  `Span.export()`. `set_trace_processors([_NullTracingProcessor()])` runs
  at import time so no span data ever leaves the process (no network call,
  regardless of whether `OPENAI_API_KEY` is set).
- **`cost.py`** — `PRICE_TABLE_USD_PER_MILLION_TOKENS`, a static dict keyed
  by model name; `compute_cost_usd(model, tokens_in, tokens_out)` is pure
  arithmetic, no network lookups, rounded to 6 decimal places.
  `UnknownModelPriceError` on a model with no table entry — a missing price
  is fatal, never a silent zero-cost default.
- **`audit.py`** — `write_audit_record()` writes one row to an `audit_log`
  SQLite table **and** appends a matching line to a JSONL mirror.
  **Redaction runs inside the writer**: every string field (`decision`,
  `approver_ref`, `policy_pack_version`, each `evidence_id`) is passed
  through `scrub()` — the same bare-10-digit / bare-13-19-digit regex
  convention as the Governed Data Layer's leak gate
  (`tests/unit/test_pii_leak.py`) — before it touches either the SQLite row
  or the JSONL line, so a careless caller cannot bypass it by constructing
  the record itself. The prompt is **never stored**: `write_audit_record()`
  takes the raw `prompt` text only to SHA-256 it in memory
  (`hash_prompt()`); only `prompt_hash` is persisted.

### Why `telemetry/` owns its own SQLite file, not the Governed Data Layer's

`data/schema.sql` already has an `audit_log` table (from Phase 0 scaffold),
but with a different, incompatible shape (`audit_id, actor_ref, action,
target_ref, occurred_at, detail` — no `prompt_hash`, `evidence_ids`,
`policy_pack_version`, `approver_ref`, or `trace_id`) and it is never
populated by any `data/` code. Reusing it was rejected: the Phase 3 brief
says this track is independent and "depends only on `contracts/`", and the
Governed Data Layer's only connection factory for anything outside
`data/seed/generate.py` is deliberately **read-only**
(`db.py::get_readonly_connection`, `file:...?mode=ro`) — an audit writer
needs a writable connection, which `data/` intentionally does not export.
So `audit.py` defines its own `SCHEMA_SQL` (also named `audit_log`, but
with the Phase 3 column set) against its own DB file
(`Settings.audit_db_path`, env `CHURNGUARD_AUDIT_DB_PATH`, default
`churnguard_audit.db`) plus its own JSONL mirror path
(`Settings.audit_log_path`, env `CHURNGUARD_AUDIT_LOG_PATH`, default
`churnguard_audit.jsonl`). **Flag this if a later phase (approval/execution)
expects a single unified audit store** — as built, `data/schema.sql`'s
`audit_log` table and `telemetry/audit.py`'s `audit_log` table are two
different tables in two different database files that happen to share a
name.

## Agents SDK template (`agents/`, `orchestration/`, `tools/`, built Phase 4)

Proves the OpenAI Agents SDK pattern end-to-end on exactly one agent
(Customer 360) so every later agent (Conversation, Competitor, Offer
Policy renderer, Supervisor) copies it rather than re-deriving it. Installed
SDK: `openai-agents==0.22.3` (`pyproject.toml`'s `>=0.0.19` floor predates a
lot of API surface used here — check the installed version before writing
code against a different one, per the phase brief).

- **`orchestration/context.py`** — `RunContext`, injected via
  `RunContextWrapper[RunContext]`. Typed with `request: CustomerContextRequest`
  because this phase builds exactly one agent; a later phase giving another
  agent its own request shape should generalize this (union or a second
  variant) rather than overload it. `evidence: list[EvidenceRef]` is a
  mutable side channel — tools append to it as they read; nothing in
  `contracts/` has room for evidence accumulated mid-run, so it has to live
  here until `orchestration/runner.py` reads it back at the end.
  `db_path` is descriptive metadata, not a live handle: every repository
  function re-resolves `CHURNGUARD_DB_PATH` from the environment on every
  call (`data/db.py::resolve_db_path`), so there is no connection object to
  actually inject — same reasoning as Phase 3's separate audit DB decision
  (extending, not fighting, a design `data/`/`config.py` already committed to).
- **`orchestration/runner.py`** — `run_once()`, a thin wrapper: exactly one
  `Runner.run()` call, timed by one `AgentSpan`, assembled into
  `AgentResult[T]`. No retry, no deadline — those are layered on top in
  `agents/base.py`, so a future caller that genuinely wants a single
  unretried call still has one.
- **`agents/base.py`** — `AgentSpec[T]` + `build_agent()` + `run_agent()`,
  the shared factory/invocation template. `run_agent()` retries exactly once
  on `agents.ModelBehaviorError` (the SDK's own exception for
  malformed/schema-invalid model JSON — there is no built-in retry for this
  in the SDK itself), converting a still-failing second attempt into
  `SchemaViolationError`; wraps the whole (both-attempts) call in
  `asyncio.wait_for(..., timeout=RunContext.deadline_ms / 1000)`, converting
  a timeout into `DeadlineExceededError`. Both are typed exceptions a caller
  can catch — `run_agent()` never lets a bare SDK exception or a
  partially-assembled `AgentResult` escape. `build_agent()` wraps
  `spec.output_type` in `AgentOutputSchema(..., strict_json_schema=False)`:
  OpenAI's *strict* structured-output mode requires every JSON object's keys
  enumerable up front, which `AccountContext.usage_by_line` (keyed by a line
  ref that varies per account) cannot satisfy — non-strict mode still
  validates the model's JSON against the full pydantic schema
  (`extra="forbid"` and all), it just forgoes the provider-side schema
  constraints strict mode adds on top. `model_override` (on both
  `build_agent()` and `run_agent()`) swaps in a test double for the
  *invocation* while `spec.model` (a plain string) stays what telemetry/cost
  bills against.
- **`tools/customer_tools.py`** — one `@function_tool` per `AccountContext`
  domain (`billing`, `payment_history`, `plan_profile`, `device_financing`,
  `usage`, `promotions`), plus `get_account_summary` (identity fields —
  `account_ref`/`tenure_months`/`line_count`/`verification_results`/
  `excluded_fields` — which aren't gated behind any domain). Every domain
  tool checks `ctx.context.request.requested_domains` **before** touching a
  repository and raises `DomainNotAuthorizedError` if refused; the SDK's own
  tool-error handling turns a raised exception into a tool-output message
  the model sees, never a crash of the run. The domain checked is fixed per
  tool, not something the model supplies as an argument, so there's nothing
  for an adversarial tool call to manipulate. Every tool returns
  **pre-serialized JSON text** (`.model_dump_json()` / `json.dumps(...)`),
  not a bare pydantic model or list: the SDK's default tool-output
  stringification is `str(value)` (Python repr, not JSON) unless the tool
  declares `output_type=`/`output_json_schema=`, and that path additionally
  requires the *top-level* shape to be a JSON object, which rejects the
  several tools here that return a bare list — pre-serializing sidesteps
  both problems uniformly. Each tool wraps its repository call in its own
  child `AgentSpan` (`model="tool_call"`, a dedicated zero-price
  `cost.py` table entry — a tool call never invokes an LLM, so its cost is a
  constant $0, not a token computation), nested under
  `RunContext.agent_span_id`.
- Multiple tool calls issued by the model in the same turn run
  **concurrently** (confirmed empirically against the installed SDK version:
  three 200ms-sleeping tools in one turn completed in ~360ms total, not
  ~600ms) — this is why `get_account_summary` composing the full
  `customer_repo.get_account_context()` (≈9 sequential 40ms-latency queries)
  alongside six other tools making their own additional queries still meets
  the <700ms p95 budget: wall-clock cost is the slowest branch, not the sum.

### Test doubles (`tests/support/fake_model.py`)

No real network/model calls happen in the test suite. Two `agents.Model`
test doubles, since the SDK ships none itself:
`ScriptedModel` (replays a fixed ordered list of turns regardless of
conversation content — used to script a deliberately malformed final turn
for the retry test) and `ToolCallingEchoModel` (a "well-behaved" fake:
turn 1 calls every tool the agent has; once every call has a
`function_call_output`, it hands a caller-supplied `assemble_output`
callback a `{tool_name: parsed_json}` mapping and returns the result as the
final message — this is what the golden tests use to exercise the real
tool → repository → data wiring for 10 different seeded accounts without
hardcoding per-account expected JSON or depending on a live model).
`tests/conftest.py`'s session-scoped `seeded_db` fixture moved up from
`tests/unit/conftest.py` in this phase so `tests/golden/` (and any future
top-level test package) gets it too, without duplicating it.

## Conversation agent & prompt-injection defence (`agents/conversation.py`, `guardrails/`, `orchestration/windowing.py`, built Phase 5)

Turns a live transcript into typed signals (`ConversationSignals`). Unlike
Customer 360, this agent *interprets* — but everything it reads is
customer-authored, hence untrusted, so defence is structural, not a prompt
added on top.

### Two-layer prompt-injection defence (`guardrails/injection.py`)

1. **`sanitize_transcript()`** runs *before* any transcript text is
   embedded in a prompt. A small, deterministic, regex-based classifier
   (never an LLM — an LLM classifier would be circularly vulnerable to the
   very attack it's meant to catch, same reasoning as the Offer Policy
   engine keeping verdicts out of model hands) tags every turn `allow` /
   `allow_with_quarantine` / `block`. A quarantined or blocked turn's raw
   text is replaced with a neutral placeholder naming only its
   `sha256` content hash — the raw text never reaches a prompt, structurally,
   not by instruction. `allow_with_quarantine` turns still let the run
   continue (genuine signals elsewhere in the window are still extracted);
   the matching verification task (`f"verify_{category}"`) is merged into
   `ConversationSignals.verification_tasks` **deterministically by code**
   after the run, never left to model compliance.
2. **`transcript_injection_guardrail`**, a real `@input_guardrail` /
   `InputGuardrail` (`run_in_parallel=False`, confirmed empirically against
   the installed SDK: a sequential guardrail that trips its tripwire
   results in **zero** calls to the model). It does not re-classify text —
   the dangerous content it would need to see has already been redacted by
   (1) — it enforces the verdict (1) already computed, read from
   `RunContext.guardrail_verdict` (the caller sets this before `Runner.run`,
   since the SDK invokes guardrail functions itself and they can't accept
   extra arguments). `verdict == "block"` trips the tripwire;
   `agents/base.py::run_agent` converts the resulting
   `InputGuardrailTripwireTriggered` into `InputBlockedError` (never
   retried — the same input would trip the same guardrail again).
   `agents/conversation.py::run_conversation_agent` catches that and
   returns a degraded `AgentResult` (`status="insufficient_evidence"`,
   `data=None`) rather than propagating an exception to its caller.

Both layers exist because either alone is fragile: sanitization alone has
no hard stop if a future caller forgets to wire the guardrail in; the
guardrail alone can only allow/deny, it can't express "continue, with this
part redacted." Pattern set and rationale for each category live in
`injection.py`'s module docstring; the 30-case adversarial corpus is
`guardrails/corpus/injections.jsonl`, one JSON object per line
(`id`, `category`, `verdict`, `text`) — extend the patterns and the corpus
together when a new attack shape is found.

### `agents/base.py` extensions (additive, Customer 360 unaffected)

`AgentSpec` gained `input_guardrails: list[InputGuardrail] = []`, threaded
into `Agent(input_guardrails=...)`. `run_agent()` gained one more
exception mapping: `InputGuardrailTripwireTriggered` → `InputBlockedError`
(carries the guardrail's `output_info`), checked before the existing
`ModelBehaviorError` retry logic and never retried itself.

### `RunContext` generalized (flagged for exactly this in Phase 4)

`RunContext.request` is now `CustomerContextRequest | ConversationInput`
(`AgentRequest` alias, `orchestration/context.py`). Nothing generic
(`agents/base.py`, `orchestration/runner.py`) ever reads `.request` — only
agent-specific code does, and must narrow first;
`tools/customer_tools.py::_customer_request()` is the one narrowing point
for Customer 360. Also gained `guardrail_verdict: str | None` — the
pre-computed-verdict side channel described above, generic enough for any
future agent with its own `@input_guardrail`.

### Transcript windowing (`orchestration/windowing.py`)

`WindowState(processed_turn_count, prior_signals)` + `next_window()` +
`advance()`. Each call to the Conversation agent gets only the turns since
the last processed point (`ConversationInput.transcript_window` already
models "a window", not "the transcript so far" — this module is just the
bookkeeping of where the last one ended) plus `prior_signals`, the
previous window's own structured output — the model is instructed to treat
its new output as the cumulative picture for the whole call, merging
`prior_signals` with what's new rather than starting over. This is what
keeps input size flat as a call runs long, instead of growing with total
call length.

### Span references are computed, not asked for

`agents/conversation.py::build_input_text()` prefixes every rendered
transcript line with its own `[MM:SS]`-format span reference (elapsed time
since `window_start_ts`), computed in code. Instructions tell the model to
copy these labels verbatim into `span_refs`, never to compute its own —
LLMs are unreliable at timestamp arithmetic; pushing anything code can do
deterministically out of the model's hands is the same philosophy as the
Offer Policy engine and Customer 360's tool-does-the-fetching design.

## Competitor agent & offer generation (`agents/competitor.py`, `offers/`, `tools/competitor_tools.py`, built Phase 6)

Grounds competitive comparison in curated, timestamped snapshots — never
live scraping — and generates the candidate offers that feed the (already
built, Phase 2) deterministic Offer Policy engine.

### `offers/normalizer.py` — pure functions, no model, no I/O

Same philosophy as the Offer Policy engine and Conversation agent's
span-ref computation: anything code can compute deterministically stays
out of the model's hands, so **the LLM must never compute a price**.

- `normalize_like_for_like_monthly()` — a competitor's per-line headline
  price → a like-for-like *total* for the account's own line count:
  `base = per_line_price × line_count`, `+ HOTSPOT_PARITY_ADDON_USD (10.00)`
  when the snapshot's `includes` lacks hotspot data, `+ estimated taxes/fees`
  (`ESTIMATED_TAX_RATE_BY_GEOGRAPHY`, a small curated table like
  `policy/packs/*.yaml`'s limits — 0.19 for `TX-DFW`, 0.15 default),
  computed on the *base* monthly, not the addon-adjusted figure. Reference
  case ($30/line × 4 lines, no hotspot, TX-DFW): 120.00 base + 10.00 addon +
  22.80 tax = **152.80**; against a 198.43 current bill that's a **-45.63**
  monthly delta — the same -45.63 Phase 2 hardcoded for its C4 fixture
  (`tests/unit/policy_fixtures.py`), which is exactly the link Phase 6
  reproduces end-to-end via `offers/generator.py`.
- `compute_switching_costs()` / `compute_breakeven_months()` — plain
  addition and division; breakeven is `None` (not infinite/negative) when
  there's no positive monthly saving. Rounded to 1 decimal place (a rough
  estimate, unlike currency amounts rounded to 2dp).
- `reconcile_claim()` — takes the customer's claim plus a list of
  `RateOption`s (label, per-line rate, line count) and finds the nearest
  one to the claimed price. If the nearest option's line count doesn't
  match the account's own, the claim is judged against a *different
  pricing tier* than what the account would actually get (e.g. a
  single-line rate quoted against a 4-line family plan) → `verdict:
  "unverifiable"`, with an explanation naming which tier it actually
  matched. Otherwise: within `CLAIM_MATCH_TOLERANCE_USD` (0.50) of the
  account's own applicable rate → `"confirmed"`; claim higher →
  `"overstated"`; lower → `"understated"`.
- Freshness/confidence-penalty thresholds are **independent of, and
  deliberately stricter than**, `data/freshness.py`'s 29/40-day boundary:
  `FRESH_MAX_DAYS=29`, `AGING_MAX_DAYS=30` (so `age_days=41` → `"stale"`).
  Competitor *pricing* moves faster than account data, so a comparison
  over a month old is already stale, not merely aging. Penalty is a
  non-negative magnitude to subtract from confidence (`fresh`→0.0,
  `aging`→0.03, `stale`→0.08), matching
  `CompetitorComparison.confidence_penalty`'s `Field(ge=0.0, le=1.0)` —
  the phase brief's "-0.08" is the *effect*, not the stored sign.

### `agents/competitor.py` — compute-then-override, same pattern as Phase 5's verification_tasks merge

`run_competitor_agent()` runs the LLM (which sees only
`tools/competitor_tools.py`'s two snapshot-query tools — no network tool,
no customer-data tool bound; checked by
`tests/unit/test_generator.py::test_competitor_agent_has_no_network_capable_tool`
via an AST import scan of `tools/competitor_tools.py`, same convention as
`test_policy_engine.py`'s no-data-import check), then **overwrites** every
numeric sub-field of the resulting `CompetitorComparison`
(`resolved_offers`, `normalization`, `switching_costs`, `breakeven_months`,
`claim_reconciliation`, `snapshot`, `freshness`, `confidence_penalty`) with
values computed by `offers/normalizer.py` from the real snapshot data —
never left to model compliance. When the resolved snapshot's freshness is
`"stale"`, `AgentResult.status` is overridden to `"stale"` and confidence
is reduced by `confidence_penalty` — the staleness penalty is emitted by
this agent, in one place, not decided later by a Supervisor.

### `CompetitorQuery` contract addition (confirmed during Phase 6)

The Competitor agent has no customer-data tool (only
`data/competitor_repo` via `tools/competitor_tools.py`), so it cannot look
up an account's device-financing payoff itself. `CompetitorQuery`
(`contracts/competitor.py`) gained `current_monthly: float` (required —
there's no sensible default for the baseline the agent compares against)
and two zero-defaulted, backward-compatible optional fields,
`known_device_financing_payoff` and `known_one_time_switching_fees` — facts
the caller (eventually the Supervisor, which will already have run
Customer 360) hands in directly, the same way `ConversationInput` carries
`prior_signals` pre-computed rather than re-derived. `make schemas` was
rerun; only `schemas/churnguard.contracts.competitor.CompetitorQuery.json`
changed.

### `offers/generator.py` — deterministic, rule-based, never imports `churnguard.policy`

`generate_candidates(account, signals, competitor)` proposes up to four
candidates, in a fixed order, renumbered `C1..` from whichever actually
apply (no gaps):

1. **Credit reinstatement** — for every `reversible=True` cause in
   `AccountContext.billing.delta_attribution`
   (`REVERSIBLE_CAUSE_COMPONENTS` maps known cause names to
   `RET_LOYALTY_CREDIT_REINSTATEMENT` / `BIL_AUTOPAY_DISCOUNT_RESTORE`,
   falling back to a generic `RET_` code for an unrecognized cause).
   Fully data-driven — the only category derived from real account
   figures rather than a curated template.
2. **Plan migration** — triggered by a non-empty `churn_signals` on a
   `_PLUS`-tier plan; amount is a curated template constant
   (`PLAN_MIGRATION_HOTSPOT_REDUCTION_MONTHLY = -38.44`), not derived from
   account data — there's no plan-tier price table that produces this
   figure from first principles, same as a policy pack limit being a
   configured ceiling rather than a computed one.
3. **Retention bundle** — triggered by non-empty `device_financing`;
   `RET_LOYALTY_CREDIT` (-20.00, 12mo) + `FIN_DEVICE_CREDIT` (-18.00,
   duration = the financed line's `months_remaining`) — again curated
   template amounts, not derived.
4. **Competitor price match** — triggered when the cheapest
   `CompetitorComparison.resolved_offers` entry's (already like-for-like
   adjusted) `monthly_price` is below `AccountContext.billing.current_bill`;
   emits `PRC_COMPETITOR_PRICE_MATCH` at exactly that delta. **This
   category is proposed here and blocked by the policy engine** (PRO-007
   below `regional_manager` tier, RET-002 if the discount is too big a
   percentage of the bill) — the separation between proposing and
   evaluating is the point, not a bug to fix.

Every emitted `OfferComponent.code` is checked against
`KNOWN_COMPONENT_PREFIXES` (`RET_`, `BIL_`, `PLN_`, `FIN_`, `PRC_`) before
being returned — an unrecognized prefix raises, rather than silently
passing through the policy engine ungoverned (see "Offer component
convention" in the Phase 2 section above).

For the exact worked-example inputs (ACCT_****4471's real billing/
financing shape, a churn signal, and the -45.63 reference competitor
delta), `generate_candidates()` reproduces Phase 2's C1–C4 fixtures
(`tests/unit/policy_fixtures.py`) byte-for-byte — verified by
`tests/unit/test_generator.py`. `account_digest_from_context()` builds the
`PolicyEvaluationRequest.account_digest` dict directly from
`AccountContext` (`financing_active` = non-empty `device_financing`,
`payment_current` = zero `current_past_due`).

## Supervisor orchestration & ranking (`agents/supervisor.py`, `orchestration/bounded.py`, `orchestration/aggregate.py`, `offers/ranker.py`, built Phase 7)

The milestone phase: reconciles the Conversation, Customer 360 and
Competitor agents into one ranked, evidence-cited, policy-validated
`RecommendationSet` in under 2 seconds. `SupervisorInput.orchestration_mode`
(frozen contract field) has two real implementations:

- **`bounded_pipeline`** (`orchestration/bounded.py`) — plain code decides
  fan-out, the hard policy filter and the ranking; no LLM anywhere in that
  decision path. Pipeline: guardrail → conversation → `asyncio.gather`
  (customer, competitor) → generator → `policy.evaluate` → rank → render →
  assemble. This is the only mode `tests/e2e/` exercises.
- **`open_harness`** (`agents/supervisor.py::build_supervisor_tools` /
  `SUPERVISOR_SPEC`) — a real `Agent` on `gpt-4.1`, the one LARGE-tier
  model in ChurnGuard (every specialist stays on `gpt-4.1-mini`), whose
  tools ARE the three specialist agents wired in via `Agent.as_tool(...)` —
  agents-as-tools, never handoffs, so the Supervisor can reconcile several
  partial views itself instead of losing its own turn to one sub-agent.
  Exploratory, not exercised by `tests/e2e/`; a known, documented
  limitation is called out in the module docstring (`RunContext.request` is
  one field per run, so a Supervisor actually driving this mode end-to-end
  would need a per-tool-call context scheme beyond what Phase 4–6 built).

### Real fan-out, not a false one (Phase 6 assumption superseded)

Phase 6 assumed the Competitor agent's `current_monthly` /
`known_device_financing_payoff` would already be available because
"the Supervisor... will already have run Customer 360" first — sequential,
not concurrent. Phase 7's brief explicitly requires
`asyncio.gather(customer, competitor)`, which supersedes that assumption:
`orchestration/bounded.py` does one fast, direct Governed Data Layer read
(`customer_repo.get_account_context`, no LLM) to seed the `CompetitorQuery`
before dispatching the Customer 360 **agent** and the Competitor agent
concurrently. This means two account-context reads happen per call (one
direct, one via the Customer 360 agent's own tools) — a deliberate,
documented trade-off for genuine parallelism, verified by the FAN-OUT TEST
(`tests/e2e/test_latency_budget.py`), which injects artificial model
latency and asserts the two agents' spans overlap in wall-clock time.

### Confidence aggregation (`orchestration/aggregate.py`)

`BASE_CONFIDENCE = 1.0` plus an itemised list of `ConfidenceAdjustment`
reasons — never a single opaque number:

- Missing Customer 360 evidence that corroborates a customer-flagged
  concern (the exact line the customer says is failing has no usage
  telemetry on file) gets a small named penalty
  (`MISSING_USAGE_CORROBORATION_PENALTY = 0.03`), never the larger generic
  one — CLAUDE.md's Phase 7 brief calls this out explicitly ("corroborating
  evidence, not a dead end").
- Any other missing evidence gets `GENERIC_MISSING_EVIDENCE_PENALTY = 0.10`.
- Stale/aging competitor pricing surfaces `CompetitorComparison.confidence_penalty`
  as its own named reason.
- A conversation run blocked by the prompt-injection guardrail
  (`status="insufficient_evidence"`) gets `BLOCKED_CONVERSATION_PENALTY = 0.5`.

`overall_confidence = clamp(BASE_CONFIDENCE + sum(delta for adjustments), 0, 1)`
by construction, so `base_confidence + sum(adjustments) == overall_confidence`
exactly — verified by `tests/e2e/test_bounded_pipeline.py`. Bounded
re-request (`MAX_REREQUEST_ATTEMPTS = 1` per agent) is a hard-coded loop
count in `orchestration/bounded.py`, never left to model judgement:
`aggregate.has_uncorroborated_gap` decides whether a gap is worth the one
extra attempt (a corroborated gap never triggers one).

`unresolved_concerns` with `customer_flagged_separate=true` become
`mandatory_actions` via `aggregate.build_mandatory_actions` — a
network/service keyword match becomes a structured
`"open_network_ticket:<line_ref>:<location>"` action; anything else becomes
a generic `"escalate_concern:<issue>"`. Never folded into a priced offer.

### Ranking (`offers/ranker.py`)

Candidates are classified into a `CandidateKind` from their component code
prefixes (`RET_`/`BIL_` → `credit_reinstatement`, `PLN_` → `plan_migration`,
`FIN_` → `retention_bundle`, `PRC_` → `competitor_price_match`), then
scored `retention_likelihood(kind) × margin`, where margin is the account's
**real remaining monthly bill** after the credit
(`current_monthly + total_monthly_impact`), not a normalized percentage.
Confidence per recommendation is a *separate* table
(`CONFIDENCE_BY_KIND`), reflecting data-groundedness rather than business
priority — a retention bundle's `FIN_` component is a real, verified
device-financing balance, so it scores *higher* on confidence than a plan
migration's flat template amount, even though it ranks lower on retention
likelihood. For the ACCT_****4471 worked example (a churn signal, a
reversible billing cause, and a stale-but-not-cheap-enough RivalCo
snapshot), this produces C1 (credit reinstatement, -28.00) ranked above C2
(plan migration, -38.44) despite C2's bigger discount — see
`offers/ranker.py`'s module docstring for the worked score derivation.

### Render step: the one LLM call in the bounded pipeline

`agents/supervisor.py::render_recommendation_copy` is the LARGE model's
only role in `bounded_pipeline` mode: given already-computed, already-
policy-cleared facts (monthly deltas, new monthly bills, disclosure codes,
the reconciliation between what the customer believes their bill is and
what it actually is), it writes `title`/`rationale`/`talk_track` prose —
the same "compute deterministically, LLM renders text around it" pattern
as the Offer Policy pack's disclosure text and the Competitor agent's
narrative framing. It can never change a rank, a price, or a verdict. A
render failure or timeout is caught and never propagates — `bounded.py`
falls back to templated copy per candidate and records this in
`fallbacks_applied`, so a text-rendering hiccup can never block a
recommendation.

### Reconciliation scenario used by `tests/e2e/`

`tests/e2e/support.py` builds two named scenarios against real seeded data
(no fabricated `CompetitorComparison` payloads):

- **Reference scenario** (ACCT_****4471): a bill-increase dispute ($50
  believed vs. $33.23 actual), a network complaint on line 3 — the
  account's own null-usage line, deliberately corroborating — in Frisco,
  TX, and a RivalCo price claim. `carriers=["RivalCo"]` deliberately
  resolves to the seeded, deliberately-**stale** RivalCo snapshot (a real
  tie-break in the seeded data — see `support.py`'s module docstring for
  the exact mechanism), which normalizes *above* the account's bill, so no
  competitor-price-match candidate is generated here — this scenario
  exercises ranking, confidence adjustments and stability, not the policy
  hard filter.
- **MetroWave scenario**: a churn-signal-only call naming MetroWave, whose
  cheapest normalized offer genuinely undercuts the bill — a real
  `PRC_COMPETITOR_PRICE_MATCH` candidate is generated and PRO-007 blocks it
  at tier 1. This is what `tests/e2e/test_blocked_offer_leak.py` uses.

## Approval gate, execution boundary & API surface (`approval/gate.py`, `approval/store.py`, `execution/service.py`, `api/routes.py`, built Phase 8)

The objective: make "agents cannot commit" **structurally** true, not a
documented convention someone could accidentally violate. A
`RecommendationSet` is not a transaction — only a human's `ApprovalDecision`
plus the execution boundary's own independent checks can make one.

### The import wall (the actual enforcement mechanism)

`execution/service.py` imports **only** `churnguard.contracts` and
`churnguard.data` (the latter just for `data.masking`'s pure, I/O-free
masked-ref shape check) plus stdlib/`aiosqlite` — never
`churnguard.agents`, `churnguard.orchestration`, `churnguard.offers`, and
not even `churnguard.approval`. `tests/architecture/test_no_imports.py`
walks the AST of every `.py` file in each package (never a grep, never an
actual import) and fails the build both directions: execution/ importing
one of those packages, or one of those packages (plus `tools/`) importing
`churnguard.execution`. This is the load-bearing guarantee, not
`api/routes.py` calling things in the right order — `api/routes.py` is
the one module allowed to import everything, but nothing downstream of it
trusts that it did its job correctly.

`execution/service.py::_validate` therefore **duplicates** several checks
`approval/gate.py` already made at approval time. That duplication is the
point: `approval` and `recommendation` are explicit parameters to
`execute()`, not values `execution/service.py` looks up itself (a real
separate execution service receives a fully-resolved authorization payload
from whatever called it; it does not reach into another service's private
storage) — passing `None` for either is exactly how "missing or unknown
approval_ref" and "recommendation_set could not be resolved" surface as
rejections. `tests/architecture/test_no_imports.py`'s
`test_a_rogue_tool_cannot_successfully_invoke_execution` proves the dynamic
half of this: nothing at the Python level stops a tool body from importing
`churnguard.execution` directly, but it still can't succeed, because no
agent tool has ever been given a channel to a real `ApprovalDecision`
(`RunContext` carries no such field).

### The five mandatory rejections (`execution/service.py::_validate`)

1. Missing or unknown `approval_ref` (caller passed `approval=None`).
2. `approver_tier < approval_tier_required` for the matching `Recommendation`.
3. An `ExecutionOperation.params["offer_id"]` differing from
   `ApprovalDecision.selected_offer_id`.
4. `RecommendationSet.commitment_status != "none"` — always `"none"` by
   construction under normal use (`Literal["none"]`), but this contract
   has no `validate_assignment=True`, so a corrupted/tampered instance
   (plain attribute assignment) is still possible and still rejected;
   `tests/unit/approval_fixtures.py::make_recommendation_set`'s
   `corrupt_commitment_status` param is how the tests simulate this.
5. Any `Recommendation.required_disclosures` code missing from
   `ApprovalDecision.disclosures_read`.

Plus two additional, non-required defense-in-depth guards: a referenced
approval whose `decision != "approved"`, and a malformed `account_ref`.

### `ExecutionOperation.params` vocabulary (decided this phase, per the Phase 0 flag)

`ExecutionOperation` (`contracts/approval.py`) was explicitly flagged in
Phase 0 as a minimal shape for a later phase to define the real vocabulary
of. `ExecutionRequest` itself has no offer-id field, so Phase 8 defines the
convention: every operation's `params` dict is expected to carry an
`"offer_id"` key naming which recommendation it executes — this is what
makes rejection path 3 above checkable at all. Not a contract change
(`params: dict[str, Any]` already allowed this); a documented convention,
same as `offers/generator.py`'s component-code-prefix convention.

### Idempotency (`execution/service.py`'s own SQLite ledger)

`execute()` owns its own SQLite file (`Settings.execution_db_path`, env
`CHURNGUARD_EXECUTION_DB_PATH`) — same "own file, own schema" pattern as
`telemetry/audit.py` and `approval/store.py`, and for the same reason
(the Governed Data Layer's connections are read-only). Replaying an
already-seen `idempotency_key` returns the **original** stored
`ExecutionResult` unconditionally — before any validation runs — even if
the replayed call's `request`/`approval`/`recommendation` arguments differ
from the first call's (`tests/unit/test_idempotency.py`'s
`test_replay_never_double_applies_even_with_a_different_second_request`
exercises exactly this). "Never a double credit."

### `approval/store.py` — immutable by omission

No update or delete function exists in this module's public API at all —
immutability is enforced by what isn't there, not by a database trigger.
`persist_approval_decision` additionally refuses to overwrite an existing
`approval_ref` row (defensive; a genuine collision would require a
SHA-256 collision) and mints the `approval_ref` itself at persistence time
— `ApprovalDecision` carries no such id, the same way `RecommendationSet`
doesn't self-assign `recommendation_set_id` until `orchestration/bounded.py`
mints one.

### `api/routes.py` — the audit point, not the security boundary

`POST /calls/{id}/recommend` runs the bounded pipeline (Phase 7) and
caches the resulting `RecommendationSet` in a process-local in-memory dict
keyed by `recommendation_set_id` — there is no dedicated recommendation-set
store yet (out of scope; nothing in this phase's brief asked for one). A
later phase needing cross-process/durable lookup should replace this, not
build around it. `POST /approvals` runs `approval/gate.py` before
persisting (a 422 with reasons if it fails) and `POST /execute` looks up
the approval and recommendation set to hand to `execution/service.py`.
**An audit row is written for every approval and execution decision,
including every rejection and escalation** — never only for a happy path.

## Agent Desktop UI (`ui/agent_desktop.py`, `api/app.py`, built Phase 9)

Objective: make the system legible to a human agent under call pressure —
explicitly called out as disproportionately important to perceived
quality. Built and tested **entirely against a static fixture JSON**
first, per the phase brief's own instruction; live integration is
strictly last, and is a manual smoke script (README.md), not an automated
test, since `POST /calls/{id}/recommend` needs a real `OPENAI_API_KEY`.

### `fixtures/recommendation_sets/ref_call_88213.json` — a real pipeline dump, not hand-authored

Generated by actually running `orchestration/bounded.run_bounded_pipeline`
against the Phase 7 ACCT_****4471 reference scenario (same transcript as
`tests/e2e/support.py::reference_transcript`, with hand-written realistic
render copy standing in for a live Supervisor-render LLM call — the
generation script isn't committed, matching how `fixtures/transcripts/`'s
existing fixtures are static data, not regenerated on every run) and
capturing `telemetry.tracer.export_trace(trace_id)` for the same run. The
fixture's shape is this phase's own design, not a frozen contract:

```
{call_id, account_summary: {account_ref, current_monthly, ...},
 transcript_window: [TranscriptTurn, ...],
 conversation_signals: ConversationSignals,
 recommendation_set: RecommendationSet,
 trace_export: <telemetry.tracer.export_trace() shape>}
```

`transcript_window`/`conversation_signals`/`trace_export` all exist
because the UI's three panes each need data `RecommendationSet` alone
doesn't carry: `TraceContext` (contracts/recommendation.py) has only
`agent_calls: list[str]` — names, no numbers — so the RIGHT pane's
per-agent latency/tokens/cost has to come from a companion trace export;
the LEFT pane's transcript-with-inline-signals needs the original
transcript and signals, neither of which survive into `RecommendationSet`.

### Trace panel: not every `agent_calls` entry has a span

`orchestration/bounded.py` appends `"policy_engine"` to
`trace.agent_calls`, but policy evaluation is plain deterministic code —
no `AgentSpan` is ever opened for it. `ui/agent_desktop.py::compute_trace_rows`
handles this explicitly: it always emits one row per name in
`agent_calls`, in order, and synthesizes a zero-cost/zero-latency row with
`has_span=False` for any name with no matching span in `trace_export`,
rather than silently dropping it. This is what ACCEPTANCE 3 ("trace panel
totals match the RecommendationSet.trace values") means concretely: the
set of rows shown always equals `trace.agent_calls` exactly, and every
total is a straight sum over those rows.

### The five NON-NEGOTIABLE UI rules, and where each lives

All in `ui/agent_desktop.py`, as plain (Streamlit-free) functions so
`tests/ui/test_disclosure_gating.py` can test most of them without
`streamlit.testing.v1.AppTest` at all:

- `is_approve_enabled` — Approve is disabled until every
  `required_disclosure` code has a ticked checkbox; an offer with none is
  enabled immediately.
- `approve_button_label` — "Escalate", never "Approve", whenever the
  sidebar's "Your approval tier" is below the offer's
  `approval_tier_required`.
- `stale_banner_text` — appears exactly when `fallbacks_applied` is
  non-empty (not merely because a stale-competitor confidence adjustment
  exists on its own — staleness alone isn't a fallback); the stated
  snapshot age is parsed out of whichever `confidence_adjustments` reason
  mentions one, since `fallbacks_applied` itself never carries a number.
- `render_offers_pane` iterates `recommendation_set["recommendations"]`
  only — there is no code path anywhere in this module that reads
  `blocked_candidates` — verified by an AppTest run against a fixture
  copy with a synthetic blocked candidate injected, asserting its id and
  reason never appear anywhere in the rendered output.
- `render_mandatory_actions_strip` renders before/above the offer cards
  via `st.error`, structurally separate from any offer card.
- `is_masked_identifier` is a display-time sanity re-check (not the
  Governed Data Layer's own masking guarantee, which already holds
  upstream) applied to every evidence id before it's shown.

### Testing strategy: pure functions directly, rendered widgets via AppTest

`ui/agent_desktop.py`'s `if __name__ == "__main__": main()` guard means
importing the module (as every test does) never runs the Streamlit app —
only `streamlit run` or `AppTest.from_file()` (confirmed empirically: it
does execute code under this guard) do. This gave the test suite two
speeds: fast, no-Streamlit-runtime tests against `build_offer_card`,
`is_approve_enabled`, `approve_button_label`, `stale_banner_text`,
`compute_trace_rows` directly, and slower `AppTest`-driven tests for
anything that requires an actually-rendered widget (a button's `disabled`
state cannot be verified any other way). `ui/__init__.py` makes `ui/` an
importable package the same way `tests/__init__.py` already makes `tests/`
one — both rely on the repo root being on `sys.path`, already true before
this phase (established by `tests/e2e`'s `from tests.support...` imports).

### `api/app.py`'s one addition beyond wiring Phase 7 + Phase 8

`GET /traces/{trace_id}` — a thin, read-only echo of
`telemetry.tracer.export_trace()`. Not part of Phase 8's three endpoints;
added here because the Phase 9 UI's live-mode trace panel needs it and it
touches neither approval nor execution.

## Phase plan (actual sequence, as given phase-by-phase — supersedes any earlier guess)

Each phase brief so far has been delivered independently and hasn't matched
the Phase 0 draft guess below phase 1, so stop guessing ahead: treat only
0-2 as fixed fact, and update this list from the actual brief each time a
new phase arrives rather than trusting the remainder.

0. **Done.** Scaffold + full contract set + fixtures + CI.
1. **Done.** Governed Data Layer (`data/`) — see above.
2. **Done.** Offer Policy engine (`policy/`) — see "Offer Policy engine"
   below. Independent of `data/`: consumes `account_digest: dict`, never a
   repository.
3. **Done.** Telemetry spine (`telemetry/`) — see "Telemetry spine" below.
   Independent of `data/` and `policy/`: consumes only `contracts/`.
4. **Done.** Customer 360 agent (`agents/customer.py`) + the shared
   agents-SDK template (`agents/base.py`, `orchestration/`, `tools/customer_tools.py`)
   every later agent copies — see "Agents SDK template" below.
5. **Done.** Conversation agent (`agents/conversation.py`) + prompt-injection
   defence (`guardrails/injection.py`) + transcript windowing
   (`orchestration/windowing.py`) — see "Conversation agent & prompt-injection
   defence" below. Extends the Phase 4 template (input_guardrails on
   `AgentSpec`, `InputBlockedError`) rather than inventing a second one.
6. **Done.** Competitor agent (`agents/competitor.py`) + curated snapshot
   normalization/switching-costs/breakeven (`offers/normalizer.py`) +
   candidate offer generation (`offers/generator.py`) — see "Competitor
   agent & offer generation" below.
7. **Done.** Supervisor orchestration (`agents/supervisor.py`,
   `orchestration/bounded.py`, `orchestration/aggregate.py`,
   `offers/ranker.py`) — see "Supervisor orchestration & ranking" below.
   The milestone phase: first end-to-end RecommendationSet, reconciling all
   four specialist agents under a hard policy filter.
8. **Done.** Approval gate + persistence (`approval/gate.py`,
   `approval/store.py`) and the execution boundary
   (`execution/service.py`) + the FastAPI surface (`api/routes.py`) — see
   "Approval gate, execution boundary & API surface" below. Structurally
   enforces "agents cannot commit": execution/ shares no imports with
   agents/, orchestration/ or offers/, checked by an AST-walking CI gate
   (`tests/architecture/test_no_imports.py`).
9. **Done.** Agent Desktop UI (`ui/agent_desktop.py`) + `api/app.py` wiring
   Phase 7/8 into one runnable app — see "Agent Desktop UI" below. Built
   and tested entirely against a static fixture
   (`fixtures/recommendation_sets/ref_call_88213.json`) per the phase
   brief's explicit instruction; live integration is a manual smoke
   script (README.md), not automated, since it needs a real
   `OPENAI_API_KEY`.
10-11. Not yet specified. Original draft guess (`experiments/` for offline
   tuning) is unconfirmed — don't plan around it.
11. `experiments/` for offline tuning, if a later phase brief still wants it.

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
make typecheck   # uv run mypy --strict src/churnguard/contracts src/churnguard/data src/churnguard/policy
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

## Phase 2 acceptance status

- `pytest` passes (118 tests total; 46 new in `tests/unit/test_policy_*.py`):
  the exact reference scenario (C1-C4) reproduced byte-for-byte,
  determinism over 1000 iterations, rule-ID coverage (self-contained,
  order-independent — see `test_policy_rules.py`), mutating each of the
  4 pack limits changes both `policy_pack_hash` and the full
  `PolicyVerdictSet` output, and malformed `account_digest` variants are
  all rejected with `PolicyDigestError`.
- `mypy --strict` passes on `src/churnguard/contracts`, `src/churnguard/data`
  **and** `src/churnguard/policy` (Makefile/CI updated).
- `ruff check` passes on `src`, `tests`, `scripts`.
- Confirmed independent of `data/`: zero `churnguard.data` imports anywhere
  under `policy/` (AST-checked, not just grepped).
- Out of scope, as specified: no agent wrapper, no data access, no LLM.

## Phase 3 acceptance status

- `pytest` passes (140 tests total; 22 new — `tests/unit/test_tracer.py`,
  `test_cost.py`, `test_audit_redaction.py`, and the new
  `tests/integration/` package's `test_span_tree.py`): a three-level nested
  `AgentSpan` call produces a correctly-shaped, correctly-parented
  `export_trace` tree (plus a sibling-branch case); the SCRUBBING TEST logs
  poisoned payloads containing a 16-digit card number and a 10-digit
  account number through every string field of an audit record and asserts
  neither appears in the SQLite file's raw bytes nor the JSONL mirror;
  fixed-price-table cost arithmetic is hand-verified for zero tokens, exact
  million-token rates, mixed counts, and 6-decimal rounding, plus an
  unknown-model `KeyError` subclass; a prompt is asserted absent from both
  storage forms while its SHA-256 (`hash_prompt`) is asserted present;
  audit rows are read back through a **fresh** `aiosqlite` connection to
  the same file path (simulating a process restart) and compare equal to
  what was written.
- `mypy --strict` passes on `src/churnguard/contracts`, `src/churnguard/data`,
  `src/churnguard/policy` **and** `src/churnguard/telemetry` (Makefile/CI
  updated).
- `ruff check` passes on `src`, `tests`, `scripts`.
- Confirmed independent: `telemetry/` imports only `contracts/` and
  `config.py`, never `churnguard.data` or `churnguard.policy`.
- Out of scope, as specified: no agents, no UI dashboards — spine only.

## Phase 4 acceptance status

- `pytest` passes (179 tests total; 39 new — `tests/golden/test_customer_agent.py`
  (parametrized over the first 10 seeded accounts), `tests/unit/test_agent_retry.py`,
  `tests/unit/test_tool_authorization.py`): `Runner.run` produces a
  schema-valid `AgentResult[AccountContext]` for `ACCT_****4471` matching
  the seeded values exactly (bill 198.43, delta 33.23, three
  `delta_attribution` entries, financing payoff 312.40); that account's
  `usage_by_line.LINE_****03` is `null`, asserted to produce
  `status="partial"` with `missing_evidence=["usage_by_line.LINE_****03"]`;
  tool calls are asserted to appear as child spans (via `export_trace`)
  under the agent's own span, each with `model="tool_call"`,
  `cost_usd=0.0`, and a real measured latency; p95 latency across the 10
  accounts is asserted under 700ms with `DB_LATENCY_MS=40`
  (measured ≈520ms); a `ScriptedModel` returning malformed JSON twice is
  asserted to retry exactly once (`model.call_count == 2`) then raise
  `SchemaViolationError` chained from the SDK's `ModelBehaviorError`,
  never a bare crash; a slow model is asserted to raise
  `DeadlineExceededError`, not a bare `asyncio.TimeoutError`; every domain
  tool is asserted to raise `DomainNotAuthorizedError` — and to leave
  `RunContext.evidence` untouched — when its domain is absent from
  `requested_domains`, and to succeed when it's present;
  `get_account_summary` is asserted never gated behind any domain.
- `mypy --strict` passes on `src/churnguard/contracts`, `src/churnguard/data`,
  `src/churnguard/policy`, `src/churnguard/telemetry`, `src/churnguard/orchestration`,
  `src/churnguard/agents` **and** `src/churnguard/tools` (Makefile/CI updated).
  One Phase 3 fix needed along the way: `AgentSpan.__aexit__`'s return type
  was `bool`; mypy strict can't prove a `with`-block's lone `return` is
  reached unless `__aexit__` is typed `Literal[False]` (otherwise it
  conservatively assumes the exception the `return`'s expression might
  raise could be swallowed, and flags a "missing return"), so it was
  narrowed — no behavior change, `__aexit__` always returned `False` already.
- `ruff check` passes on `src`, `tests`, `scripts`.
- Known pre-existing flake, not introduced by this phase: `tests/unit/test_db_access.py::test_db_latency_ms_is_injected_per_query`
  occasionally fails (`elapsed_ms` under its 3ms floor for a nominal 5ms
  `asyncio.sleep`) when run in the same session as other async-heavy test
  files — reproduces even paired with unrelated pre-existing Phase 1/2
  files, so it's Windows event-loop timer-resolution flakiness in a
  tight wall-clock assertion, not a Phase 4 regression. Left as-is
  (Phase 1 is "done"); flag if it starts failing CI regularly.
- `tests/conftest.py` now holds the shared `seeded_db` fixture (moved from
  `tests/unit/conftest.py`) so `tests/golden/` gets it too.
- Out of scope, as specified: no other agents, no Supervisor, no
  orchestration beyond a single run.

## Phase 5 acceptance status

- `pytest` passes (292 tests total; 113 new —
  `tests/unit/test_injection_guardrail.py`, `test_windowing.py`,
  `tests/golden/test_conversation_agent.py`, `test_conversation_variance.py`,
  `tests/integration/test_conversation_windowing.py`): the brief's exact
  reference transcript reproduces byte-for-byte (3 intents, `unit="ambiguous"`,
  `bill_increase` 50.00 `precision="approximate"`, `customer_flagged_separate=true`,
  and `[01:24]` quarantined into `verification_tasks=["verify_prior_commitment_claim"]`);
  the ADVERSARIAL GATE runs all 30 corpus cases through the real
  `classify_text` → `sanitize_transcript` → `build_input_text` →
  `run_conversation_agent` pipeline — every `block`-tier case is asserted
  to never reach the model (`Model.get_response` raises `AssertionError`
  if called) and every case's raw text is asserted absent from the
  rendered `<transcript>` block; the two known-injection fixtures (03, 11)
  are asserted blocked and the other 10 are asserted not blocked;
  multi-intent fixtures are asserted to carry ≥2 intents with distinct
  `span_refs`; windowed input size is asserted flat (<20-char band) across
  a simulated 15-turn call, contrasted against the un-windowed alternative
  (which grows >10x over the same span); a 10-run variance check asserts
  the intent **set** is identical across runs and confidence values fall
  in a range, never asserting an exact float — see that test's docstring
  for why a scripted model (no live model is available in this
  environment) is the honest limit of what "variance" can mean here.
- `mypy --strict` passes on `src/churnguard/contracts`, `src/churnguard/data`,
  `src/churnguard/policy`, `src/churnguard/telemetry`, `src/churnguard/orchestration`,
  `src/churnguard/agents`, `src/churnguard/tools` **and** `src/churnguard/guardrails`
  (Makefile/CI updated).
- `ruff check` passes on `src`, `tests`, `scripts`.
- Followed the Phase 4 template rather than inventing a second one:
  `AgentSpec`/`build_agent`/`run_agent` gained `input_guardrails` and
  `InputBlockedError` (additive; Customer 360's spec is unaffected, its
  `input_guardrails` defaults to empty); `RunContext.request` generalized
  to a union exactly as flagged in Phase 4's CLAUDE.md notes.
- Out of scope, as specified: no Supervisor, no offers, no competitor
  logic.
- Fixed a `.gitignore` bug from Phase 3: the blanket `*.jsonl` rule (meant
  only for the audit JSONL mirror) was silently excluding
  `guardrails/corpus/injections.jsonl`. Narrowed to
  `/churnguard_audit.jsonl` (the actual default mirror path).

## Phase 6 acceptance status

- `pytest` passes (329 tests total; 37 new — `tests/unit/test_normalizer.py`,
  `test_generator.py`, `tests/integration/test_generator_to_policy.py`):
  every `offers/normalizer.py` function is hand-verified to 2dp against the
  phase brief's exact worked example (152.80 like-for-like monthly, -45.63
  delta, 452.40 switching costs, 9.9 breakeven months, a claim reconciled
  against the wrong pricing tier); freshness/confidence-penalty boundary
  tests at exactly 29/30/31 days; `offers/generator.py::generate_candidates`
  reproduces Phase 2's C1–C4 fixtures byte-for-byte for the matching
  worked-example inputs, and is separately checked to skip each category
  when its trigger condition doesn't hold (no reversible cause, no churn
  signal, no financing, no competitor saving); an unknown component-code
  prefix is asserted to raise rather than pass through ungoverned; the
  competitor agent's tool list is asserted to contain exactly the two
  snapshot-query tools, with an AST scan of `tools/competitor_tools.py`
  confirming no network-library import and no `churnguard.data` import
  beyond `competitor_repo`; the integration suite runs the real seeded DB
  → `generate_candidates` → `policy.engine.evaluate` pipeline for all 10
  golden accounts (candidates always come back 1:1 with verdicts, including
  the zero-candidate case) and specifically re-derives Phase 2's exact
  pass/pass_with_disclosure/pass/blocked verdict set end-to-end against a
  DB-backed `AccountContext`.
- `mypy --strict` passes on `src/churnguard/contracts`, `src/churnguard/data`,
  `src/churnguard/policy`, `src/churnguard/telemetry`, `src/churnguard/orchestration`,
  `src/churnguard/agents`, `src/churnguard/tools`, `src/churnguard/guardrails`
  **and** `src/churnguard/offers` (Makefile/CI updated).
- `ruff check` passes on `src`, `tests`, `scripts`.
- Confirmed independent, per the brief: `offers/generator.py` never imports
  `churnguard.policy` (it proposes candidates; the already-built policy
  engine evaluates them; C4 is the deliberate example of that separation).
  `tools/competitor_tools.py` imports only `churnguard.data.repositories`
  (specifically `competitor_repo`) — no other domain repo, no network
  library.
- One contract addition, confirmed during this phase rather than asked of
  the user up front (it doesn't touch a frozen field's type, only adds new
  ones): `CompetitorQuery` gained a required `current_monthly` and two
  zero-defaulted optional `known_*` switching-cost inputs — see
  "Competitor agent & offer generation" above for why. `RunContext.request`
  (`orchestration/context.py`) extended to a three-member union exactly as
  flagged in Phase 5's notes. `make schemas` rerun; only
  `schemas/churnguard.contracts.competitor.CompetitorQuery.json` changed —
  verified via `git diff --stat schemas/` before committing.
- Out of scope, as specified: no Supervisor, no ranking, no approval.

## Phase 7 acceptance status

- `pytest` passes (352 tests total; 23 new — `tests/e2e/test_bounded_pipeline.py`,
  `test_latency_budget.py`, `test_blocked_offer_leak.py`, plus shared
  scenario builders in `tests/e2e/support.py`): all 12 fixture transcripts
  produce schema-valid `RecommendationSet`s against the real seeded DB
  (blocked/prompt-injection fixtures 03/11 degrade to the empty-signals
  fallback rather than failing); the ACCT_****4471 reference scenario ranks
  C1 above C2 above C3 (retention likelihood × margin, not discount size —
  C2's -38.44 discount is bigger than C1's -28.00 but still ranks lower);
  the reconciliation checks (mandatory action for the Frisco/line-3
  concern, the $50-believed-vs-$33.23-actual note, corroboration vs. a
  bounded re-request) all hold; `overall_confidence` is asserted equal to
  `BASE_CONFIDENCE + sum(adjustment deltas)` exactly; the top-ranked
  `offer_id` is asserted identical across 5 repeated runs; the LEAK TEST
  serializes the `RecommendationSet` with `blocked_candidates` excluded and
  asserts the blocked id and its `PRC_COMPETITOR_PRICE_MATCH` component
  never appear in the rest of the payload, for a real (not fabricated)
  MetroWave-scenario blocked candidate; the FAN-OUT TEST injects artificial
  model latency into the customer and competitor model doubles and asserts
  their `AgentSpan`s overlap in wall-clock time via `export_trace`; p95
  latency across 10 runs (with `DB_LATENCY_MS=40`) is asserted under 2000ms
  (measured comfortably under 1s).
- `mypy --strict` passes on every package the Makefile already covered
  (`contracts`, `data`, `policy`, `telemetry`, `orchestration`, `agents`,
  `tools`, `guardrails`, `offers`) — no Makefile change needed, since
  Phase 6 already listed `orchestration`/`agents`/`offers` in full.
- `ruff check` passes on `src`, `tests`, `scripts`.
- No contract changes this phase — `BlockedCandidate`'s existing minimal
  shape (`candidate_id`, `reason`, `governing_rules`, no priced components)
  turned out to already be exactly what the LEAK TEST needed; the reference
  brief's "visible_to_agent: false" language describes that existing
  shape's effect, not a new field.
- Confirmed independent design decisions made in-package (not asked of the
  user, since none touch a frozen contract) — see "Supervisor orchestration
  & ranking" above for the full reasoning on each: the real-fan-out
  bootstrap read superseding Phase 6's sequential assumption; the
  confidence-aggregation formula and its constants; the
  retention-likelihood/confidence-by-kind tables in `offers/ranker.py`; the
  mandatory-action keyword classifier; and the render step's fallback
  contract (a rendering failure degrades to templated copy, logged in
  `fallbacks_applied`, never propagated).
- Known pre-existing flake, not introduced by this phase (same one flagged
  in Phase 4's notes): `tests/unit/test_db_access.py::test_db_latency_ms_is_injected_per_query`
  occasionally fails under Windows event-loop timer-resolution noise;
  reproduced in isolation during this phase's verification, unrelated to
  any Phase 7 change. Left as-is.
- Out of scope, as specified: no approval UI, no execution, no A/B harness.

## Phase 8 acceptance status

- `pytest` passes (384 tests total; 32 new — `tests/unit/test_approval_gate.py`
  (10 tests), `test_approval_store.py` (3, not in the brief's explicit
  list but added for basic persistence/immutability coverage),
  `test_execution_rejections.py` (10: the five required rejection paths,
  one happy path, three additional defense-in-depth guards),
  `test_idempotency.py` (4), `tests/architecture/test_no_imports.py`
  (3: execution/ importing agents/orchestration/offers, the reverse for
  agents/orchestration/offers/tools, and the rogue-tool runtime test)):
  every one of the five mandatory rejection paths has its own passing
  negative test against `execution.service.execute()` called directly
  with plain contract objects; the idempotency replay tests confirm a
  second call with the same `idempotency_key` returns the byte-identical
  original result even when given deliberately different (and, alone,
  invalid) second-call arguments; the IMPORT-GRAPH TEST walks the AST of
  `execution/`, `agents/`, `orchestration/`, `offers/` and `tools/` and
  fails on any forbidden cross-import in either direction; the rogue-tool
  test defines a tool body that imports `churnguard.execution` directly
  and calls it with `approval=None` (the honest worst case — no tool has
  ever been given a real `ApprovalDecision`) and asserts the call is
  rejected, never accepted.
- `mypy --strict` passes on every package the Makefile now covers,
  extended this phase to include `approval`, `execution` and `api`
  (66 source files total).
- `ruff check` passes on `src`, `tests`, `scripts`.
- No contract changes this phase. `ExecutionOperation.params`'s vocabulary
  (an `"offer_id"` key) was defined per the Phase 0 flag that this shape
  was minimal and Phase 8/9's to fill in — see "Approval gate, execution
  boundary & API surface" above; `params: dict[str, Any]` already allowed
  this without a type change.
- Two new `Settings` fields (`approval_db_path`, `execution_db_path`,
  envs `CHURNGUARD_APPROVAL_DB_PATH` / `CHURNGUARD_EXECUTION_DB_PATH`) —
  additive, following the exact precedent `audit_db_path`/`audit_log_path`
  set in Phase 3.
- Verified via a manual FastAPI `TestClient` smoke run (not a committed
  test, since the brief's explicit test list didn't ask for one and
  `POST /calls/{id}/recommend` needs a real or heavily-mocked LLM to
  exercise meaningfully): approve → execute → replay (no-op) → reject an
  unknown `approval_ref`, all behaving as designed.
- Out of scope, as specified: no UI beyond the three API endpoints.

## Phase 9 acceptance status

- `pytest` passes (402 tests total; 18 new — `tests/ui/test_disclosure_gating.py`):
  ACCEPTANCE 1 (renders from the static fixture, zero exceptions, with no
  backend running - `load_fixture` reads straight off disk and the
  default sidebar state is `live_mode=False`); ACCEPTANCE 2 (offer C2's
  Approve button — the fixture's one offer with a required disclosure —
  is asserted `disabled=True`, then `disabled=False` after checking its
  `DISC_HOTSPOT_REDUCTION` checkbox via a real `AppTest` interaction, not
  just the pure `is_approve_enabled` function in isolation); ACCEPTANCE 3
  (`compute_trace_rows`'s row names asserted to equal
  `trace.agent_calls` exactly, including a zero-cost row for
  `policy_engine`, which has no `AgentSpan`); plus the tier-2-shows-
  Escalate rule, the mandatory-actions-strip rule, the stale-banner rule
  (both "present with age stated" and "absent when fallbacks_applied is
  empty, even with a stale-competitor adjustment present" cases), the
  masked-identifiers-only rule, and blocked-candidates-never-render
  (proved via a genuine `AppTest` run against a fixture copy with a
  synthetic blocked candidate injected through the sidebar's
  `fixture_path_input`, not just the pure view-model layer).
- `mypy --strict` and `ruff` both extended to cover `ui/` (69 source files
  for mypy) and pass clean; `requests>=2.31` added to `pyproject.toml`
  (already installed transitively, but a module that imports it directly
  in production code should declare it).
- ACCEPTANCE 4 (full approve flow against the live API) verified manually
  during development, not via an automated test (matches the phase
  brief's own test list, which only asks for a documented manual script):
  an in-process `uvicorn.Server` bound to a real socket, driven entirely
  through `ui/agent_desktop.py`'s own `submit_approval_live` /
  `submit_execution_live` / `fetch_trace_live` functions (real HTTP, not
  FastAPI's `TestClient`), confirmed approve → execute (200, accepted) →
  replay (200, `replayed: true`, identical result) → reject-unknown-
  approval (422) all work end-to-end. `POST /calls/{id}/recommend` itself
  needs a real `OPENAI_API_KEY` this sandbox doesn't have — README.md's
  manual smoke script is how a human with real credentials verifies that
  last leg.
- No contract changes this phase. The fixture's shape (transcript +
  signals + recommendation_set + trace_export) is this phase's own
  design, documented above under "Agent Desktop UI" — not a contract,
  since nothing in `contracts/` needed to change to support it.
- One new `api/app.py` endpoint beyond the Phase 8 brief's three
  (`GET /traces/{trace_id}`) — additive, justified in "Agent Desktop UI"
  above, touches neither approval nor execution.
- Observed the same class of pre-existing timing flake already documented
  in Phase 4/7/8's notes, this time in a different test:
  `tests/golden/test_customer_agent.py::test_p95_latency_under_700ms_with_realistic_db_latency`
  failed once at 706ms/706ms-ish against its 700ms budget when run as
  part of the full suite, and passed cleanly in isolation and on a repeat
  full-suite run — Windows timer/scheduling noise under load, not a
  Phase 9 regression (this phase touched no code in `agents/` or
  `data/`).
- Out of scope, as specified: no auth, no multi-user, no styling system.
