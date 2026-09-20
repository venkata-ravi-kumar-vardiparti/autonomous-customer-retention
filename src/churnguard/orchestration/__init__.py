"""Supervisor fan-out/reconcile/score/rank orchestration.

Module map:
    context.py   RunContext - per-run state injected via
                 RunContextWrapper[RunContext] (trace context, deadline,
                 policy_pack_version, db_path, and the evidence side
                 channel tools append to).
    runner.py    run_once() - a thin wrapper over agents.Runner.run that
                 returns a telemetry-populated AgentResult[T]. Retry and
                 deadline logic live one layer up, in agents/base.py.
"""
