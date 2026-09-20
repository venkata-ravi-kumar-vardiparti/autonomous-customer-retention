"""The five agents (Conversation, Customer 360, Competitor, Offer Policy, Supervisor),
implemented as agents-as-tools under the Supervisor.

Module map:
    base.py        shared factory (AgentSpec, build_agent, run_agent) every
                   agent is built from - retry-once on schema violation,
                   deadline enforcement, layered on orchestration/runner.py.
    customer.py    the Customer 360 agent (Phase 4) - retrieve and structure
                   account/billing/usage data, never interpret or recommend.
    conversation.py the Conversation agent (Phase 5) - transcript -> typed
                   signals, behind a two-layer prompt-injection defence.
    competitor.py  the Competitor agent (Phase 6) - curated snapshot ->
                   normalized CompetitorComparison, no live scraping.
    supervisor.py  the Supervisor (Phase 7) - agents-as-tools wiring for
                   orchestration_mode="open_harness", plus the LARGE-model
                   render step orchestration/bounded.py calls for
                   orchestration_mode="bounded_pipeline".
"""
