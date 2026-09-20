"""RunContext: everything a single agent run needs, injected via
RunContextWrapper[RunContext] (the OpenAI Agents SDK's mechanism for
passing local, non-model-visible state into tools and hooks).

The LLM never sees this object - only what a tool's return value puts in
front of it, or what an input_guardrail decides. Fields exist as side
channels into and out of a run:

- `evidence` is appended to by every tool as it reads a repository result,
  and orchestration/runner.py reads it back once Runner.run() completes to
  assemble AgentResult.evidence. Nothing in contracts/ has room for evidence
  accumulated mid-run (AgentResult.evidence is only ever populated at the
  end), so this is where it has to live meanwhile.
- `guardrail_verdict` is written by pre-run classification (e.g.
  guardrails/injection.py's sanitization pass, run before Runner.run is
  even called) and read by a matching input_guardrail - the SDK invokes
  guardrail functions itself, so this is the only channel available to hand
  a guardrail a verdict that was already computed rather than making it
  re-derive one from input text that pre-processing may have already
  sanitized. None means "no guardrail-relevant classification for this run."

`request` generalized to a union in Phase 5 (Conversation) - flagged for
exactly this in Phase 4. Code that reads `.request` for a specific agent
(e.g. tools/customer_tools.py) must narrow it first; nothing generic in
agents/base.py or orchestration/runner.py ever touches this field.

Phase 6 (Competitor) extends the union again with CompetitorQuery, exactly
as flagged - tools/competitor_tools.py::_competitor_request narrows it the
same way tools/customer_tools.py::_customer_request already does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from churnguard.contracts.competitor import CompetitorQuery
from churnguard.contracts.conversation import ConversationInput
from churnguard.contracts.customer import CustomerContextRequest
from churnguard.contracts.envelope import EvidenceRef

AgentRequest = CustomerContextRequest | ConversationInput | CompetitorQuery


@dataclass
class RunContext:
    request: AgentRequest
    trace_id: str
    agent_span_id: str
    policy_pack_version: str
    deadline_ms: int
    db_path: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    guardrail_verdict: str | None = None


__all__ = ["AgentRequest", "RunContext"]
