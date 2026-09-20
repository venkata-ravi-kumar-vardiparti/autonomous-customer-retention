"""RunContext: everything a single agent run needs, injected via
RunContextWrapper[RunContext] (the OpenAI Agents SDK's mechanism for
passing local, non-model-visible state into tools and hooks).

The LLM never sees this object - only what a tool's return value puts in
front of it. Two fields exist purely as a side channel *out* of the run:
`evidence` is appended to by every tool as it reads a repository result,
and orchestration/runner.py reads it back once Runner.run() completes to
assemble AgentResult.evidence. Nothing in contracts/ has room for evidence
accumulated mid-run (AgentResult.evidence is only ever populated at the
end), so this is where it has to live meanwhile.

`request` is typed as CustomerContextRequest because Phase 4 builds exactly
one agent (Customer 360). A later phase that gives the Conversation or
Competitor agent its own RunContext-shaped needs should generalize this
(a union, or a second RunContext variant) rather than overload this one -
flag it here if that phase arrives and this field no longer fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from churnguard.contracts.customer import CustomerContextRequest
from churnguard.contracts.envelope import EvidenceRef


@dataclass
class RunContext:
    request: CustomerContextRequest
    trace_id: str
    agent_span_id: str
    policy_pack_version: str
    deadline_ms: int
    db_path: str
    evidence: list[EvidenceRef] = field(default_factory=list)


__all__ = ["RunContext"]
