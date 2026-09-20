"""Phase 11: quantify bounded pipeline vs open-ended harness.

Runs N per arm, round-robined across the 12 fixture transcripts (so both
"N=30 per arm" and "over the fixture transcripts" hold at once - each
fixture gets floor(N/12) or ceil(N/12) repeats), collecting the full
METRICS TO COLLECT PER RUN set: output variance (top offer ID, grouped by
(arm, fixture) so repeats of the SAME input are what's compared), total
tokens, total cost, wall-clock latency, context growth per turn (an
approximation - see _sum_span_tokens_and_cost's docstring), schema-validity
rate, policy-violation rate, tool-call count.

NON-NEGOTIABLE: the ONLY difference between the two SupervisorInput
envelopes built for a given (fixture, run_index) pair is orchestration_mode
- see _build_envelope, called once per arm with everything else identical.

Usage:
    python -m experiments.run_ab --n 30       # real models, needs OPENAI_API_KEY
    python -m experiments.run_ab --n 6 --fake # deterministic doubles, no API key needed -
                                               # see README.md's manual smoke script notes
                                               # for what --fake can and can't show.

Writes raw per-run records as JSON to --output (default
experiments/ab_results.json) for experiments/report.py to read.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from churnguard.contracts.conversation import TranscriptTurn
from churnguard.contracts.recommendation import (
    OrchestrationMode,
    RecommendationSet,
    SupervisorInput,
)
from churnguard.orchestration.bounded import run_bounded_pipeline
from churnguard.orchestration.harness import run_open_harness
from churnguard.telemetry.tracer import export_trace

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures" / "transcripts"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "ab_results.json"
DEFAULT_ACCOUNT_REF = "ACCT_****4471"
DEFAULT_POLICY_PACK_VERSION = "2026.09.1"
DEFAULT_N = 30
DEFAULT_DEADLINE_MS = 5000


@dataclasses.dataclass(frozen=True)
class RunRecord:
    arm: str
    fixture: str
    run_index: int
    top_offer_id: str | None
    total_tokens: int
    total_cost_usd: float
    latency_ms: float
    tool_call_count: int
    context_tokens_per_call: float
    schema_valid: bool
    policy_violation: bool
    error: str | None


def _sum_span_tokens_and_cost(trace_id: str) -> tuple[int, float]:
    """Walks telemetry.tracer.export_trace's full nested span tree (not
    just root spans) so tool-call sub-spans are included too - harmless
    for cost (tool calls always price at $0, telemetry/cost.py's
    TOOL_CALL_MODEL) and correct for tokens_in/out where a sub-agent call
    itself carries real usage.

    "context growth per turn" (one of the METRICS TO COLLECT) has no
    direct per-turn breakdown available from this codebase's telemetry
    (AgentSpan records one cumulative usage figure per agent call, not a
    per-turn one inside a multi-turn Runner.run - see
    orchestration/runner.py::run_once). total_tokens / tool_call_count is
    used as an honest, documented approximation: average tokens per
    orchestration step, comparable the same way across both arms, not a
    literal "tokens added on turn N vs turn N-1" curve.
    """
    total_tokens = 0
    total_cost = 0.0

    def _walk(node: dict[str, Any]) -> None:
        nonlocal total_tokens, total_cost
        telemetry = node["telemetry"]
        total_tokens += telemetry["tokens_in"] + telemetry["tokens_out"]
        total_cost += telemetry["cost_usd"]
        for child in node["children"]:
            _walk(child)

    tree = export_trace(trace_id)
    for root in tree["spans"]:
        _walk(root)
    return total_tokens, round(total_cost, 6)


def _load_fixture_transcript(path: Path) -> list[TranscriptTurn]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [TranscriptTurn(**turn) for turn in raw["transcript_window"]]


def _fixture_paths() -> list[Path]:
    paths = sorted(FIXTURES_DIR.glob("*.json"))
    if len(paths) != 12:
        raise RuntimeError(f"expected 12 fixture transcripts, found {len(paths)}")
    return paths


def _build_envelope(
    transcript: list[TranscriptTurn],
    *,
    call_id: str,
    orchestration_mode: OrchestrationMode,
    account_ref: str = DEFAULT_ACCOUNT_REF,
    agent_authority_tier: int = 1,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    policy_pack_version: str = DEFAULT_POLICY_PACK_VERSION,
) -> SupervisorInput:
    return SupervisorInput(
        call_id=call_id,
        account_ref=account_ref,
        transcript_window=transcript,
        agent_authority_tier=agent_authority_tier,
        agent_ref="AGT_****0192",
        deadline_ms=deadline_ms,
        policy_pack_version=policy_pack_version,
        orchestration_mode=orchestration_mode,
    )


RunnerFn = Callable[[SupervisorInput], Awaitable[RecommendationSet]]


async def _run_one(
    supervisor_input: SupervisorInput,
    runner: RunnerFn,
    *,
    fixture_name: str,
    run_index: int,
) -> RunRecord:
    start = time.perf_counter()
    try:
        result = await runner(supervisor_input)
        latency_ms = (time.perf_counter() - start) * 1000

        # Defensive schema re-check: a JSON round trip must still validate -
        # this is what "schema-validity rate" actually measures, not just
        # "did the call return without raising".
        RecommendationSet.model_validate_json(result.model_dump_json())

        total_tokens, total_cost = _sum_span_tokens_and_cost(result.trace.trace_id)
        tool_call_count = len(result.trace.agent_calls)
        blocked_ids = {b.candidate_id for b in result.blocked_candidates}
        offered_ids = {r.offer_id for r in result.recommendations}

        return RunRecord(
            arm=supervisor_input.orchestration_mode,
            fixture=fixture_name,
            run_index=run_index,
            top_offer_id=result.recommendations[0].offer_id if result.recommendations else None,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            latency_ms=round(latency_ms, 1),
            tool_call_count=tool_call_count,
            context_tokens_per_call=round(total_tokens / max(tool_call_count, 1), 1),
            schema_valid=True,
            policy_violation=bool(blocked_ids & offered_ids),
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - a run that raises is a data point, not a crash
        latency_ms = (time.perf_counter() - start) * 1000
        return RunRecord(
            arm=supervisor_input.orchestration_mode,
            fixture=fixture_name,
            run_index=run_index,
            top_offer_id=None,
            total_tokens=0,
            total_cost_usd=0.0,
            latency_ms=round(latency_ms, 1),
            tool_call_count=0,
            context_tokens_per_call=0.0,
            schema_valid=False,
            policy_violation=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def _fake_runners() -> dict[OrchestrationMode, RunnerFn]:
    """--fake: the same deterministic model doubles the rest of the test
    suite uses (tests/support/fake_model.py), imported lazily (via
    importlib, not a static `from tests... import`) so the real (default)
    path never needs tests/ to be importable, and so mypy's strict check
    of this package never pulls tests/support/fake_model.py's own,
    separately-scoped typing into experiments/'s - it's checked, if at
    all, by whatever tests/ itself requires, not by this package. See this
    module's docstring for what --fake can and can't demonstrate."""
    import importlib
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    support: Any = importlib.import_module("e2e.support")

    async def run_bounded(supervisor_input: SupervisorInput) -> RecommendationSet:
        return await run_bounded_pipeline(
            supervisor_input,
            conversation_model_override=support.reference_conversation_model(),
            customer_model_override=support.customer_model(),
            competitor_model_override=support.competitor_model(),
            supervisor_model_override=support.render_model_with_canned_copy(),
        )

    async def run_harness(supervisor_input: SupervisorInput) -> RecommendationSet:
        import json as _json

        from churnguard.orchestration.harness import _build_harness_tools, _EvidenceBasket

        fake_model_module: Any = importlib.import_module("tests.support.fake_model")
        tool_calling_echo_model = fake_model_module.ToolCallingEchoModel

        dummy_basket = _EvidenceBasket()
        dummy_tools = _build_harness_tools(
            supervisor_input,
            geography="TX-DFW",
            trace_id="dummy",
            basket=dummy_basket,
            conversation_model_override=None,
            customer_model_override=None,
            competitor_model_override=None,
        )

        def assemble(outputs: dict[str, Any]) -> str:
            summary = f"gathered {list(outputs)}"
            return _json.dumps({"evidence_sufficient": True, "summary": summary})

        harness_model = tool_calling_echo_model(tools=dummy_tools, assemble_output=assemble)
        return await run_open_harness(
            supervisor_input,
            conversation_model_override=support.reference_conversation_model(),
            customer_model_override=support.customer_model(),
            competitor_model_override=support.competitor_model(),
            supervisor_model_override=support.render_model_with_canned_copy(),
            harness_model_override=harness_model,
        )

    return {"bounded_pipeline": run_bounded, "open_harness": run_harness}


def _real_runners() -> dict[OrchestrationMode, RunnerFn]:
    async def run_bounded(supervisor_input: SupervisorInput) -> RecommendationSet:
        return await run_bounded_pipeline(supervisor_input)

    async def run_harness(supervisor_input: SupervisorInput) -> RecommendationSet:
        return await run_open_harness(supervisor_input)

    return {"bounded_pipeline": run_bounded, "open_harness": run_harness}


async def run_experiment(n: int, *, fake: bool) -> list[RunRecord]:
    runners = _fake_runners() if fake else _real_runners()
    fixture_paths = _fixture_paths()
    records: list[RunRecord] = []

    for arm, runner in runners.items():
        for run_index in range(n):
            fixture_path = fixture_paths[run_index % len(fixture_paths)]
            transcript = _load_fixture_transcript(fixture_path)
            envelope = _build_envelope(
                transcript,
                call_id=f"CALL_****{run_index:04d}",
                orchestration_mode=arm,
            )
            record = await _run_one(
                envelope, runner, fixture_name=fixture_path.stem, run_index=run_index
            )
            records.append(record)

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="runs per arm")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="use deterministic test doubles instead of real models (no OPENAI_API_KEY needed)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    records = asyncio.run(run_experiment(args.n, fake=args.fake))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps([dataclasses.asdict(r) for r in records], indent=2), encoding="utf-8"
    )
    print(f"wrote {len(records)} run records to {args.output}")


if __name__ == "__main__":
    main()
