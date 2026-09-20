"""Async span recorder layered over the OpenAI Agents SDK's tracing primitives.

Every churnguard agent call opens one AgentSpan:

    async with AgentSpan(
        "customer_360", model="gpt-4.1-mini", trace_id=trace_id, parent_span_id=parent_id
    ) as span:
        ...
        span.record_usage(tokens_in=120, tokens_out=40)
    span.telemetry  # -> contracts.envelope.Telemetry

Parent/child linkage is explicit (trace_id/span_id/parent_span_id passed in,
matching AgentEnvelope's own tracing fields) rather than relying on the
SDK's ambient contextvar-based "current span" - agents-as-tools fan out
concurrently, and envelopes can in principle be resumed across a process
boundary, so nesting has to survive without depending on call-stack order.
The SDK's own Trace/Span objects are still created and linked (by explicit
`parent=`, looked up from the span_id we assigned) so the integration is
real, not decorative, in case a future phase wires in an OpenAI-side
processor - but span export/tree-building for this codebase's own use
(`export_trace`) is done from our own SpanRecord store, never from the SDK's
`Span.export()`.

No network calls happen as a side effect of importing this module: it
replaces the SDK's default (network-exporting) processor list with a
local no-op processor at import time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from agents import tracing as sdk_tracing

from churnguard.contracts.envelope import Telemetry
from churnguard.telemetry.cost import compute_cost_usd

SpanStatus = str  # "ok" | "error"


@dataclass(frozen=True)
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    status: SpanStatus
    started_at: datetime
    ended_at: datetime
    telemetry: Telemetry


class _NullTracingProcessor(sdk_tracing.TracingProcessor):
    """Replaces the SDK's default exporter so no span data ever leaves the process."""

    def on_trace_start(self, trace: sdk_tracing.Trace) -> None:
        pass

    def on_trace_end(self, trace: sdk_tracing.Trace) -> None:
        pass

    def on_span_start(self, span: sdk_tracing.Span[Any]) -> None:
        pass

    def on_span_end(self, span: sdk_tracing.Span[Any]) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


sdk_tracing.set_trace_processors([_NullTracingProcessor()])


class _SpanStore:
    """Process-local span records, keyed by trace_id, in insertion order."""

    def __init__(self) -> None:
        self._records: dict[str, list[SpanRecord]] = {}

    def add(self, record: SpanRecord) -> None:
        self._records.setdefault(record.trace_id, []).append(record)

    def get(self, trace_id: str) -> list[SpanRecord]:
        return list(self._records.get(trace_id, []))

    def reset(self, trace_id: str | None = None) -> None:
        if trace_id is None:
            self._records.clear()
        else:
            self._records.pop(trace_id, None)


_STORE = _SpanStore()
_sdk_traces: dict[str, sdk_tracing.Trace] = {}
_sdk_spans: dict[str, sdk_tracing.Span[Any]] = {}


def _get_or_start_sdk_trace(trace_id: str) -> sdk_tracing.Trace:
    existing = _sdk_traces.get(trace_id)
    if existing is not None:
        return existing
    new_trace = sdk_tracing.trace("churnguard", trace_id=trace_id)
    new_trace.start(mark_as_current=False)
    _sdk_traces[trace_id] = new_trace
    return new_trace


def _resolve_sdk_parent(
    trace_id: str, parent_span_id: str | None
) -> sdk_tracing.Trace | sdk_tracing.Span[Any]:
    if parent_span_id is not None and parent_span_id in _sdk_spans:
        return _sdk_spans[parent_span_id]
    return _get_or_start_sdk_trace(trace_id)


def new_trace_id() -> str:
    return sdk_tracing.gen_trace_id()


def new_span_id() -> str:
    return sdk_tracing.gen_span_id()


class AgentSpan:
    """One traced agent call. Async context manager; never swallows exceptions."""

    def __init__(
        self,
        name: str,
        *,
        model: str,
        trace_id: str,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        cache_hit: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.trace_id = trace_id
        self.parent_span_id = parent_span_id
        self.span_id = span_id or new_span_id()
        self.cache_hit = cache_hit
        self.tokens_in = 0
        self.tokens_out = 0
        self.telemetry: Telemetry | None = None
        self._sdk_span: sdk_tracing.Span[Any] | None = None
        self._started_at: datetime | None = None
        self._start_perf = 0.0

    def record_usage(self, *, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    async def __aenter__(self) -> Self:
        parent = _resolve_sdk_parent(self.trace_id, self.parent_span_id)
        self._sdk_span = sdk_tracing.custom_span(
            self.name, data={"model": self.model}, span_id=self.span_id, parent=parent
        )
        self._sdk_span.start(mark_as_current=False)
        _sdk_spans[self.span_id] = self._sdk_span
        self._started_at = datetime.now(UTC)
        self._start_perf = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        latency_ms = int((time.perf_counter() - self._start_perf) * 1000)
        status: SpanStatus = "error" if exc_type is not None else "ok"
        cost_usd = compute_cost_usd(self.model, self.tokens_in, self.tokens_out)
        self.telemetry = Telemetry(
            model=self.model,
            latency_ms=latency_ms,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=cost_usd,
            cache_hit=self.cache_hit,
        )
        ended_at = datetime.now(UTC)

        if self._sdk_span is not None:
            if exc is not None:
                self._sdk_span.set_error({"message": str(exc), "data": {}})
            self._sdk_span.finish(reset_current=False)

        assert self._started_at is not None
        _STORE.add(
            SpanRecord(
                trace_id=self.trace_id,
                span_id=self.span_id,
                parent_span_id=self.parent_span_id,
                name=self.name,
                status=status,
                started_at=self._started_at,
                ended_at=ended_at,
                telemetry=self.telemetry,
            )
        )
        return False


def export_trace(trace_id: str) -> dict[str, Any]:
    """A nested JSON tree of every recorded span for one trace, root(s) first."""
    records = _STORE.get(trace_id)
    known_span_ids = {record.span_id for record in records}
    children_by_parent: dict[str | None, list[SpanRecord]] = {}
    for record in records:
        parent_key = (
            record.parent_span_id if record.parent_span_id in known_span_ids else None
        )
        children_by_parent.setdefault(parent_key, []).append(record)

    def _node(record: SpanRecord) -> dict[str, Any]:
        return {
            "span_id": record.span_id,
            "parent_span_id": record.parent_span_id,
            "name": record.name,
            "status": record.status,
            "started_at": record.started_at.isoformat(),
            "ended_at": record.ended_at.isoformat(),
            "telemetry": record.telemetry.model_dump(mode="json"),
            "children": [_node(child) for child in children_by_parent.get(record.span_id, [])],
        }

    roots = children_by_parent.get(None, [])
    return {"trace_id": trace_id, "spans": [_node(root) for root in roots]}


def reset_state(trace_id: str | None = None) -> None:
    """Test-only: clear recorded spans (and, for a full reset, cached SDK trace/span handles)."""
    _STORE.reset(trace_id)
    if trace_id is None:
        _sdk_traces.clear()
        _sdk_spans.clear()


__all__ = [
    "AgentSpan",
    "SpanRecord",
    "export_trace",
    "new_span_id",
    "new_trace_id",
    "reset_state",
]
