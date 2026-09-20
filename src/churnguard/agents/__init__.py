"""The five agents (Conversation, Customer 360, Competitor, Offer Policy, Supervisor),
implemented as agents-as-tools under the Supervisor.

Module map:
    base.py       shared factory (AgentSpec, build_agent, run_agent) every
                  agent is built from - retry-once on schema violation,
                  deadline enforcement, layered on orchestration/runner.py.
    customer.py   the Customer 360 agent (Phase 4) - retrieve and structure
                  account/billing/usage data, never interpret or recommend.
"""
