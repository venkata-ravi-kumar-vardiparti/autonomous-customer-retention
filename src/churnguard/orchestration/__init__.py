"""Supervisor fan-out/reconcile/score/rank orchestration.

Module map:
    context.py    RunContext - per-run state injected via
                  RunContextWrapper[RunContext] (trace context, deadline,
                  policy_pack_version, db_path, and the evidence side
                  channel tools append to).
    runner.py     run_once() - a thin wrapper over agents.Runner.run that
                  returns a telemetry-populated AgentResult[T]. Retry and
                  deadline logic live one layer up, in agents/base.py.
    windowing.py  transcript window bookkeeping for the Conversation agent
                  (Phase 5).
    aggregate.py  (Phase 7) confidence aggregation across the four
                  specialist agents into one itemised overall_confidence,
                  plus mandatory-action extraction and the bounded
                  re-request gap classifier.
    bounded.py    (Phase 7) the bounded_pipeline implementation of
                  SupervisorInput.orchestration_mode: guardrail ->
                  conversation -> asyncio.gather(customer, competitor) ->
                  generator -> policy.evaluate -> rank -> render -> assemble.
"""
