"""ChurnGuard Agent Desktop - Streamlit UI (Phase 9).

Three panes: LEFT (live transcript with extracted signals inline), CENTRE
(ranked offers as cards), RIGHT (trace panel: per-agent latency, tokens,
cost).

Built and tested ENTIRELY against a static fixture JSON
(fixtures/recommendation_sets/ref_call_88213.json) - no backend required
to develop or to satisfy ACCEPTANCE 1-3. A "Live mode" toggle in the
sidebar switches to calling the real FastAPI app (api/app.py) for
ACCEPTANCE 4 - see README.md's manual smoke script.

Every UI *decision* (is Approve enabled, does this offer say "Approve" or
"Escalate", does the stale-data banner appear, what goes in a trace row) is
a plain, Streamlit-free function below - tests/ui/test_disclosure_gating.py
exercises most of them directly (fast, no Streamlit runtime needed) and,
for the actual rendered widgets, through streamlit.testing.v1.AppTest
(which does run this module, but only past the `if __name__ == "__main__"`
guard - importing this module directly, as a test does, never triggers
`main()`).

Run with: streamlit run ui/agent_desktop.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import requests
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_PATH = REPO_ROOT / "fixtures" / "recommendation_sets" / "ref_call_88213.json"
DEFAULT_API_BASE_URL = "http://localhost:8000"

# --- masking sanity: this UI must never format an unmasked identifier in ---
# --- - see is_masked_identifier(), used defensively at render time.       ---
_MASKED_ID_RE = re.compile(r"^(?:[A-Z0-9]+_)+(\*\*\*\*)?[0-9A-Za-z]+$")


def is_masked_identifier(value: str) -> bool:
    """True for ChurnGuard's masked ref shapes (ACCT_****4471, CALL_****8213,
    LINE_****03, EVID_..., AGT_****0192, APR_...). Best-effort - this is a
    display-time sanity check, not the Governed Data Layer's own masking
    boundary (data/masking.py), which already guarantees this upstream."""
    return bool(_MASKED_ID_RE.match(value))


# =============================================================================
# Pure view-model logic - no Streamlit import below this line until the
# "Streamlit rendering" section.
# =============================================================================


@dataclass(frozen=True)
class OfferCardViewModel:
    rank: int
    offer_id: str
    title: str
    new_monthly: float
    saving: float
    rationale: str
    confidence: float
    confidence_tooltip: str
    evidence_ids: list[str]
    disclosure_codes: list[str]
    disclosure_texts: dict[str, str]
    approval_tier_required: int
    hold_condition: str | None


def _confidence_tooltip(adjustments: list[dict[str, Any]]) -> str:
    if not adjustments:
        return "No confidence adjustments for this call."
    lines = [f"{a['reason']}: {a['delta']:+.2f}" for a in adjustments]
    return "Confidence adjustments for this call:\n" + "\n".join(lines)


def build_offer_card(
    recommendation: dict[str, Any],
    *,
    current_monthly: float,
    confidence_adjustments: list[dict[str, Any]],
) -> OfferCardViewModel:
    """recommendation/confidence_adjustments are plain dicts (already-parsed
    RecommendationSet JSON), not contracts.recommendation.Recommendation
    instances - this module never imports churnguard.contracts, so it works
    identically against the static fixture and a live API response without
    caring whether the caller happened to validate the payload first."""
    monthly_delta = recommendation["customer_impact"]["monthly_delta"]
    new_monthly = round(current_monthly + monthly_delta, 2)
    saving = round(-monthly_delta, 2)
    disclosures = recommendation["required_disclosures"]
    return OfferCardViewModel(
        rank=recommendation["rank"],
        offer_id=recommendation["offer_id"],
        title=recommendation["title"],
        new_monthly=new_monthly,
        saving=saving,
        rationale=recommendation["rationale"],
        confidence=recommendation["confidence"],
        confidence_tooltip=_confidence_tooltip(confidence_adjustments),
        evidence_ids=list(recommendation["evidence_ids"]),
        disclosure_codes=[d["code"] for d in disclosures],
        disclosure_texts={d["code"]: d["text"] for d in disclosures},
        approval_tier_required=recommendation["approval_tier_required"] or 0,
        hold_condition=recommendation["hold_condition"],
    )


def is_approve_enabled(disclosure_codes: list[str], checked_codes: set[str]) -> bool:
    """NON-NEGOTIABLE: the Approve button is disabled until every
    required_disclosure checkbox is ticked. An offer with no required
    disclosures is enabled immediately - there is nothing to acknowledge."""
    return all(code in checked_codes for code in disclosure_codes)


ApprovalActionLabel = Literal["Approve", "Escalate"]


def approve_button_label(approval_tier_required: int, user_tier: int) -> ApprovalActionLabel:
    """NON-NEGOTIABLE: a tier-2 offer shows "Escalate", never "Approve", to
    a tier-1 user - i.e. whenever the user's own tier doesn't meet the
    offer's required tier."""
    return "Approve" if user_tier >= approval_tier_required else "Escalate"


_AGE_DAYS_RE = re.compile(r"(\d+)d old")


def stale_banner_text(
    fallbacks_applied: list[str], confidence_adjustments: list[dict[str, Any]]
) -> str | None:
    """NON-NEGOTIABLE: a stale-data banner appears whenever fallbacks_applied
    is non-empty, with the snapshot age stated. fallbacks_applied itself
    (orchestration/aggregate.py) never carries a numeric age - it's a
    prose trail of what was re-requested or fell back - so the age is
    pulled from whichever confidence_adjustments reason mentions one (the
    competitor-staleness reason orchestration/aggregate.py emits, e.g.
    "competitor pricing is stale (42d old)"). None (no banner) is the
    correct answer when fallbacks_applied is empty, even if a stale
    competitor adjustment exists on its own - staleness alone isn't a
    fallback."""
    if not fallbacks_applied:
        return None

    age_days: str | None = None
    for adjustment in confidence_adjustments:
        match = _AGE_DAYS_RE.search(adjustment["reason"])
        if match:
            age_days = match.group(1)
            break

    message = "Some data behind this recommendation is stale or was backfilled: " + "; ".join(
        fallbacks_applied
    )
    if age_days is not None:
        message += f" (competitor pricing snapshot is {age_days} days old)"
    return message


@dataclass(frozen=True)
class TraceRow:
    name: str
    model: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    has_span: bool


@dataclass(frozen=True)
class TraceTotals:
    rows: list[TraceRow] = field(default_factory=list)
    total_latency_ms: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0


def compute_trace_rows(agent_calls: list[str], trace_export: dict[str, Any]) -> TraceTotals:
    """ACCEPTANCE 3: the set of agent names shown here always matches
    RecommendationSet.trace.agent_calls exactly, in the same order - not
    every name has a matching AgentSpan (policy_engine is plain
    deterministic code with no LLM call and no span), which shows as a
    real, zero-cost row rather than being silently dropped."""
    spans_by_name = {span["name"]: span for span in trace_export.get("spans", [])}
    rows: list[TraceRow] = []
    for name in agent_calls:
        span = spans_by_name.get(name)
        if span is not None:
            telemetry = span["telemetry"]
            rows.append(
                TraceRow(
                    name=name,
                    model=telemetry["model"],
                    latency_ms=telemetry["latency_ms"],
                    tokens_in=telemetry["tokens_in"],
                    tokens_out=telemetry["tokens_out"],
                    cost_usd=telemetry["cost_usd"],
                    has_span=True,
                )
            )
        else:
            rows.append(
                TraceRow(
                    name=name, model="-", latency_ms=0, tokens_in=0, tokens_out=0,
                    cost_usd=0.0, has_span=False,
                )
            )

    return TraceTotals(
        rows=rows,
        total_latency_ms=sum(r.latency_ms for r in rows),
        total_tokens=sum(r.tokens_in + r.tokens_out for r in rows),
        total_cost_usd=round(sum(r.cost_usd for r in rows), 6),
    )


def _format_span_ref(turn_ts: str, window_start_ts: str) -> str:
    """Mirrors agents/conversation.py::_format_span_ref (private there) so
    this UI can line signals back up against transcript turns using the
    exact same [MM:SS] labels the Conversation agent itself produced -
    reimplemented, not imported, since that helper is private to its
    module and this is a display-only concern, not a shared contract."""
    turn = datetime.fromisoformat(turn_ts.replace("Z", "+00:00"))
    start = datetime.fromisoformat(window_start_ts.replace("Z", "+00:00"))
    total_seconds = max(int((turn - start).total_seconds()), 0)
    minutes, seconds = divmod(total_seconds, 60)
    return f"[{minutes:02d}:{seconds:02d}]"


@dataclass(frozen=True)
class TranscriptLineViewModel:
    span_ref: str
    speaker: str
    text: str
    inline_signals: list[str]


def build_transcript_lines(
    transcript_window: list[dict[str, Any]], conversation_signals: dict[str, Any]
) -> list[TranscriptLineViewModel]:
    """Only Intent and CompetitorClaim carry span_refs in the frozen
    ConversationSignals contract - Concern/StatedFigure/ChurnSignal do not,
    so only the former two can be shown truly inline; the rest render in a
    per-window summary instead (see render_transcript_pane)."""
    if not transcript_window:
        return []
    window_start_ts = transcript_window[0]["ts"]

    signals_by_span_ref: dict[str, list[str]] = {}
    for intent in conversation_signals.get("intents", []):
        for span_ref in intent["span_refs"]:
            signals_by_span_ref.setdefault(span_ref, []).append(f"intent: {intent['type']}")
    for claim in conversation_signals.get("competitor_claims", []):
        for span_ref in claim["span_refs"]:
            label = f"competitor claim: {claim['carrier']} ${claim['price']:.2f} ({claim['unit']})"
            signals_by_span_ref.setdefault(span_ref, []).append(label)

    lines = []
    for turn in transcript_window:
        span_ref = _format_span_ref(turn["ts"], window_start_ts)
        lines.append(
            TranscriptLineViewModel(
                span_ref=span_ref,
                speaker=turn["speaker"],
                text=turn["text"],
                inline_signals=signals_by_span_ref.get(span_ref, []),
            )
        )
    return lines


def load_fixture(path: Path | str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


# =============================================================================
# Live-mode HTTP calls against api/app.py - thin, no retry/deadline logic
# of its own (this is a manual-smoke-test integration, not a production
# client).
# =============================================================================


def fetch_recommendation_live(
    api_base_url: str, *, call_id: str, body: dict[str, Any], timeout: float = 30.0
) -> dict[str, Any]:
    url = f"{api_base_url}/calls/{call_id}/recommend"
    response = requests.post(url, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def fetch_trace_live(api_base_url: str, trace_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    response = requests.get(f"{api_base_url}/traces/{trace_id}", timeout=timeout)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def submit_approval_live(
    api_base_url: str, decision_body: dict[str, Any], *, timeout: float = 10.0
) -> requests.Response:
    return requests.post(f"{api_base_url}/approvals", json=decision_body, timeout=timeout)


def submit_execution_live(
    api_base_url: str, execution_body: dict[str, Any], *, timeout: float = 10.0
) -> requests.Response:
    return requests.post(f"{api_base_url}/execute", json=execution_body, timeout=timeout)


# =============================================================================
# Streamlit rendering - everything below imports/uses `st`.
# =============================================================================


def render_stale_banner(
    fallbacks_applied: list[str], confidence_adjustments: list[dict[str, Any]]
) -> None:
    banner = stale_banner_text(fallbacks_applied, confidence_adjustments)
    if banner is not None:
        st.warning(banner, icon="⚠️")


def render_mandatory_actions_strip(mandatory_actions: list[str]) -> None:
    """A separate, visually distinct strip - never mixed into an offer
    card - because a mandatory action applies regardless of which offer
    (if any) the customer ends up taking."""
    if not mandatory_actions:
        return
    st.error(
        "**Mandatory actions - apply regardless of offer chosen:**\n"
        + "\n".join(f"- `{action}`" for action in mandatory_actions)
    )


def render_transcript_pane(
    transcript_window: list[dict[str, Any]], conversation_signals: dict[str, Any]
) -> None:
    st.subheader("Live transcript")
    for line in build_transcript_lines(transcript_window, conversation_signals):
        st.markdown(f"**{line.span_ref} {line.speaker}:** {line.text}")
        for signal in line.inline_signals:
            st.caption(f"→ {signal}")

    st.divider()
    st.markdown("**Extracted signals (this window)**")
    for churn_signal in conversation_signals.get("churn_signals", []):
        st.caption(f"churn signal: {churn_signal['signal']} ({churn_signal['strength']})")
    for figure in conversation_signals.get("customer_stated_figures", []):
        st.caption(
            f"customer stated: {figure['field']} = {figure['value']} ({figure['precision']})"
        )
    for concern in conversation_signals.get("unresolved_concerns", []):
        flag = " [flagged separately]" if concern["customer_flagged_separate"] else ""
        st.caption(f"unresolved concern: {concern['issue']}{flag}")


def render_offer_card(
    recommendation: dict[str, Any],
    *,
    current_monthly: float,
    confidence_adjustments: list[dict[str, Any]],
    user_tier: int,
    live_mode: bool,
    api_base_url: str,
    account_ref: str,
    recommendation_set_id: str,
    approver_ref: str,
) -> None:
    card = build_offer_card(
        recommendation,
        current_monthly=current_monthly,
        confidence_adjustments=confidence_adjustments,
    )

    with st.container(border=True):
        st.markdown(f"#### #{card.rank}  {card.title}")
        st.markdown(
            f"New monthly: **${card.new_monthly:.2f}**  |  "
            f"Saving: **${card.saving:.2f}/mo**  |  "
            f"Tier required: **{card.approval_tier_required}**"
        )
        st.caption(card.rationale)

        st.progress(min(max(card.confidence, 0.0), 1.0), text=f"Confidence {card.confidence:.0%}")
        st.caption(card.confidence_tooltip.replace("\n", " | "), help=card.confidence_tooltip)

        if card.hold_condition:
            st.info(f"Hold condition: {card.hold_condition}")

        with st.expander(f"Evidence ({len(card.evidence_ids)})"):
            for evidence_id in card.evidence_ids:
                shown = (
                    evidence_id if is_masked_identifier(evidence_id) else "[unmasked id withheld]"
                )
                st.caption(shown)

        checked_codes: set[str] = set()
        if card.disclosure_codes:
            st.markdown("**Required disclosures**")
            for code in card.disclosure_codes:
                checkbox_key = f"disclosure_{card.offer_id}_{code}"
                checked = st.checkbox(
                    card.disclosure_texts[code], key=checkbox_key, help=code
                )
                if checked:
                    checked_codes.add(code)

        approve_enabled = is_approve_enabled(card.disclosure_codes, checked_codes)
        label = approve_button_label(card.approval_tier_required, user_tier)
        button_type: Literal["primary", "secondary"] = (
            "primary" if label == "Approve" else "secondary"
        )

        clicked = st.button(
            label, key=f"approve_{card.offer_id}", disabled=not approve_enabled, type=button_type
        )
        if clicked:
            _handle_offer_decision(
                card,
                label=label,
                live_mode=live_mode,
                api_base_url=api_base_url,
                account_ref=account_ref,
                recommendation_set_id=recommendation_set_id,
                approver_ref=approver_ref,
                user_tier=user_tier,
                checked_codes=checked_codes,
            )


def _handle_offer_decision(
    card: OfferCardViewModel,
    *,
    label: ApprovalActionLabel,
    live_mode: bool,
    api_base_url: str,
    account_ref: str,
    recommendation_set_id: str,
    approver_ref: str,
    user_tier: int,
    checked_codes: set[str],
) -> None:
    decision_type = "approved" if label == "Approve" else "escalated"

    if not live_mode:
        if decision_type == "approved":
            st.success(f"(fixture mode - no backend) Offer {card.offer_id} approved.")
        else:
            st.info(
                f"(fixture mode - no backend) Offer {card.offer_id} escalated for "
                f"tier-{card.approval_tier_required} sign-off."
            )
        return

    decision_body = {
        "recommendation_set_id": recommendation_set_id,
        "decision": decision_type,
        "selected_offer_id": card.offer_id,
        "approver_ref": approver_ref,
        "approver_tier": user_tier,
        "approved_at": datetime.now(UTC).isoformat(),
        "edits": [],
        "disclosures_read": sorted(checked_codes),
        "policy_pack_version": "2026.09.1",
    }
    approval_response = submit_approval_live(api_base_url, decision_body)
    if approval_response.status_code != 200:
        st.error(f"Approval rejected: {approval_response.text}")
        return
    approval_ref = approval_response.json()["approval_ref"]
    st.success(f"Approval recorded: {approval_ref}")

    if decision_type != "approved":
        return

    execution_body = {
        "approval_ref": approval_ref,
        "account_ref": account_ref,
        "operations": [{"op_code": "apply_offer", "params": {"offer_id": card.offer_id}}],
        "idempotency_key": f"ui-{card.offer_id}-{uuid4().hex}",
    }
    execution_response = submit_execution_live(api_base_url, execution_body)
    if execution_response.status_code == 200:
        st.success(f"Executed: {execution_response.json()}")
    else:
        st.error(f"Execution rejected: {execution_response.text}")


def render_offers_pane(
    recommendation_set: dict[str, Any],
    *,
    current_monthly: float,
    user_tier: int,
    live_mode: bool,
    api_base_url: str,
    account_ref: str,
    approver_ref: str,
) -> None:
    st.subheader("Ranked offers")
    # NON-NEGOTIABLE: blocked_candidates are NEVER rendered - not greyed
    # out, absent. This loop only ever iterates `recommendations`.
    for recommendation in recommendation_set["recommendations"]:
        render_offer_card(
            recommendation,
            current_monthly=current_monthly,
            confidence_adjustments=recommendation_set["confidence_adjustments"],
            user_tier=user_tier,
            live_mode=live_mode,
            api_base_url=api_base_url,
            account_ref=account_ref,
            recommendation_set_id=recommendation_set["recommendation_set_id"],
            approver_ref=approver_ref,
        )


def render_trace_pane(trace: dict[str, Any], trace_export: dict[str, Any]) -> None:
    st.subheader("Trace")
    totals = compute_trace_rows(trace["agent_calls"], trace_export)
    for row in totals.rows:
        with st.container(border=True):
            st.markdown(f"**{row.name}**  ({row.model})")
            st.caption(
                f"{row.latency_ms} ms  |  {row.tokens_in + row.tokens_out} tokens  |  "
                f"${row.cost_usd:.6f}"
                + ("" if row.has_span else "  (no LLM span - deterministic code)")
            )
    st.divider()
    st.metric("Total latency", f"{totals.total_latency_ms} ms")
    st.metric("Total tokens", f"{totals.total_tokens}")
    st.metric("Total cost", f"${totals.total_cost_usd:.6f}")
    st.caption(f"trace_id: {trace['trace_id']}")


def main() -> None:
    st.set_page_config(page_title="ChurnGuard Agent Desktop", layout="wide")
    st.title("ChurnGuard Agent Desktop")

    with st.sidebar:
        st.header("Session")
        user_tier = st.number_input(
            "Your approval tier", min_value=0, max_value=5, value=1, step=1, key="user_tier_input"
        )
        approver_ref = st.text_input(
            "Your agent ref", value="AGT_****0192", key="approver_ref_input"
        )
        live_mode = st.checkbox("Live mode (call the API)", value=False, key="live_mode_toggle")
        api_base_url = (
            st.text_input("API base URL", value=DEFAULT_API_BASE_URL, key="api_base_url_input")
            if live_mode
            else DEFAULT_API_BASE_URL
        )
        fixture_path = st.text_input(
            "Fixture path", value=str(DEFAULT_FIXTURE_PATH), key="fixture_path_input"
        )

    data = load_fixture(fixture_path)

    recommendation_set = data["recommendation_set"]
    account_summary = data["account_summary"]

    render_stale_banner(
        recommendation_set["fallbacks_applied"], recommendation_set["confidence_adjustments"]
    )
    render_mandatory_actions_strip(recommendation_set["mandatory_actions"])

    left, centre, right = st.columns([1, 2, 1])

    with left:
        render_transcript_pane(data["transcript_window"], data["conversation_signals"])

    with centre:
        render_offers_pane(
            recommendation_set,
            current_monthly=account_summary["current_monthly"],
            user_tier=int(user_tier),
            live_mode=live_mode,
            api_base_url=api_base_url,
            account_ref=account_summary["account_ref"],
            approver_ref=approver_ref,
        )

    with right:
        trace_export = data["trace_export"]
        if live_mode:
            try:
                trace_id = recommendation_set["trace"]["trace_id"]
                trace_export = fetch_trace_live(api_base_url, trace_id)
            except requests.RequestException as exc:
                st.warning(f"Could not fetch live trace, showing fixture trace instead: {exc}")
        render_trace_pane(recommendation_set["trace"], trace_export)


if __name__ == "__main__":
    main()
