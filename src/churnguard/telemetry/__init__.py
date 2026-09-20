"""Cost/latency/token accounting and audit-trail writing. Independent of data/.

Module map:
    tracer.py   AgentSpan - async context manager layered over the OpenAI
                Agents SDK tracing primitives; produces contracts.envelope.Telemetry
                per span and export_trace(trace_id) for the nested span tree.
    cost.py     static per-model USD price table, pure arithmetic.
    audit.py    audit_log SQLite table + JSONL mirror; redaction runs at the
                writer, never trusts the caller to have scrubbed anything.
"""

from churnguard.telemetry.tracer import AgentSpan, export_trace

__all__ = ["AgentSpan", "export_trace"]
