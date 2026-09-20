"""ui/agent_desktop.py: ACCEPTANCE 1 (renders from the static fixture with
no backend), ACCEPTANCE 2 (Approve is blocked without disclosure
acknowledgement) and ACCEPTANCE 3 (trace panel totals match
RecommendationSet.trace), plus the other NON-NEGOTIABLE UI rules.

Two layers, matching ui/agent_desktop.py's own split: fast, Streamlit-free
tests against the pure view-model functions (imported directly - importing
the module never runs main(), guarded by `if __name__ == "__main__"`), and
slower streamlit.testing.v1.AppTest-driven tests against the actual
rendered widgets, which is the only way to genuinely prove a button's
`disabled` state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui import agent_desktop as app

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "recommendation_sets"
    / "ref_call_88213.json"
)
APP_PATH = Path(__file__).resolve().parents[2] / "ui" / "agent_desktop.py"


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return app.load_fixture(FIXTURE_PATH)


def _all_rendered_text(at: AppTest) -> str:
    chunks: list[str] = []
    for collection in (
        at.markdown, at.caption, at.text, at.title, at.subheader, at.header,
        at.error, at.warning, at.info, at.success, at.metric, at.json,
    ):
        for element in collection:
            chunks.append(str(getattr(element, "value", "")))
    return "\n".join(chunks)


# --- pure view-model logic ---------------------------------------------------


def test_fixture_file_is_valid_json_with_the_expected_shape(fixture_data: dict) -> None:
    assert {
        "call_id",
        "account_summary",
        "transcript_window",
        "conversation_signals",
        "recommendation_set",
        "trace_export",
    } <= set(fixture_data)
    assert fixture_data["recommendation_set"]["commitment_status"] == "none"


def test_is_approve_enabled_requires_every_disclosure_checked() -> None:
    assert app.is_approve_enabled([], set()) is True
    assert app.is_approve_enabled(["DISC_A"], set()) is False
    assert app.is_approve_enabled(["DISC_A"], {"DISC_A"}) is True
    assert app.is_approve_enabled(["DISC_A", "DISC_B"], {"DISC_A"}) is False
    assert app.is_approve_enabled(["DISC_A", "DISC_B"], {"DISC_A", "DISC_B"}) is True


def test_approve_button_label_switches_to_escalate_above_user_tier() -> None:
    assert app.approve_button_label(approval_tier_required=1, user_tier=1) == "Approve"
    assert app.approve_button_label(approval_tier_required=2, user_tier=1) == "Escalate"
    assert app.approve_button_label(approval_tier_required=0, user_tier=0) == "Approve"
    assert app.approve_button_label(approval_tier_required=2, user_tier=2) == "Approve"


def test_stale_banner_absent_when_fallbacks_applied_is_empty() -> None:
    adjustments = [{"reason": "competitor pricing is stale (42d old)", "delta": -0.08}]
    assert app.stale_banner_text([], adjustments) is None


def test_stale_banner_present_and_states_the_snapshot_age_when_fallbacks_applied() -> None:
    banner = app.stale_banner_text(
        ["customer_360 re-requested once for an uncorroborated missing-evidence gap"],
        [{"reason": "competitor pricing is stale (42d old)", "delta": -0.08}],
    )
    assert banner is not None
    assert "re-requested" in banner
    assert "42 days old" in banner


def test_stale_banner_present_without_an_age_when_none_is_parseable() -> None:
    banner = app.stale_banner_text(["some fallback happened"], [])
    assert banner is not None
    assert "some fallback happened" in banner
    assert "days old" not in banner


def test_compute_trace_rows_matches_agent_calls_exactly(fixture_data: dict) -> None:
    trace = fixture_data["recommendation_set"]["trace"]
    trace_export = fixture_data["trace_export"]

    totals = app.compute_trace_rows(trace["agent_calls"], trace_export)

    assert [row.name for row in totals.rows] == trace["agent_calls"]
    # policy_engine is deterministic code with no AgentSpan - it must still
    # show up as a real (zero-cost) row, never be silently dropped.
    policy_row = next(row for row in totals.rows if row.name == "policy_engine")
    assert policy_row.has_span is False
    assert policy_row.cost_usd == 0.0

    assert totals.total_latency_ms == sum(row.latency_ms for row in totals.rows)
    assert totals.total_tokens == sum(row.tokens_in + row.tokens_out for row in totals.rows)
    assert totals.total_cost_usd == round(sum(row.cost_usd for row in totals.rows), 6)


def test_build_offer_card_computes_new_monthly_and_saving(fixture_data: dict) -> None:
    recommendation_set = fixture_data["recommendation_set"]
    current_monthly = fixture_data["account_summary"]["current_monthly"]
    c1 = next(r for r in recommendation_set["recommendations"] if r["offer_id"] == "C1")

    card = app.build_offer_card(
        c1, current_monthly=current_monthly,
        confidence_adjustments=recommendation_set["confidence_adjustments"],
    )

    assert card.new_monthly == pytest.approx(170.43)
    assert card.saving == pytest.approx(28.00)
    assert card.disclosure_codes == []


def test_masked_identifier_check() -> None:
    assert app.is_masked_identifier("ACCT_****4471")
    assert app.is_masked_identifier("EVID_ACCT_4471")
    assert app.is_masked_identifier("LINE_****03")
    assert not app.is_masked_identifier("4471234567890123")
    assert not app.is_masked_identifier("plain text")


# --- rendered-widget behaviour (streamlit.testing.v1.AppTest) --------------


@pytest.fixture()
def running_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    return at


def test_app_renders_from_the_static_fixture_with_no_exception(running_app: AppTest) -> None:
    """ACCEPTANCE 1: no backend running - load_fixture reads straight off
    disk, and the default sidebar state is live_mode=False."""
    assert len(running_app.exception) == 0
    assert len(running_app.button) == 3  # one Approve/Escalate per recommendation


def test_approve_is_disabled_until_every_disclosure_is_checked(running_app: AppTest) -> None:
    """ACCEPTANCE 2. C2 (Switch to a lower-hotspot plan) is the fixture's
    one offer with a required disclosure (DISC_HOTSPOT_REDUCTION)."""
    approve_c2 = running_app.get_by_key("approve_C2")
    assert approve_c2.disabled is True

    checkbox = running_app.get_by_key("disclosure_C2_DISC_HOTSPOT_REDUCTION")
    checkbox.check().run()

    approve_c2 = running_app.get_by_key("approve_C2")
    assert approve_c2.disabled is False


def test_offer_with_no_disclosures_is_approve_ready_immediately(running_app: AppTest) -> None:
    approve_c1 = running_app.get_by_key("approve_C1")
    assert approve_c1.disabled is False


def test_tier_2_offer_shows_escalate_to_a_tier_1_user(running_app: AppTest) -> None:
    """NON-NEGOTIABLE: the default sidebar user tier is 1; C3 requires
    tier 2, so its button must read "Escalate", never "Approve"."""
    approve_c3 = running_app.get_by_key("approve_C3")
    assert approve_c3.label == "Escalate"


def test_tier_1_offer_shows_approve_to_a_tier_1_user(running_app: AppTest) -> None:
    approve_c1 = running_app.get_by_key("approve_C1")
    assert approve_c1.label == "Approve"


def test_mandatory_actions_render_as_a_distinct_strip(running_app: AppTest) -> None:
    assert len(running_app.error) == 1
    assert "open_network_ticket:LINE_****03:Frisco, TX" in running_app.error[0].value
    assert "Mandatory actions" in running_app.error[0].value


def test_stale_banner_absent_for_the_reference_fixture(
    running_app: AppTest, fixture_data: dict
) -> None:
    """The shipped reference fixture has empty fallbacks_applied - no
    banner should render, even though it DOES have a stale-competitor
    confidence adjustment (staleness alone isn't a fallback)."""
    assert fixture_data["recommendation_set"]["fallbacks_applied"] == []
    assert len(running_app.warning) == 0


def test_only_masked_evidence_ids_ever_render(running_app: AppTest) -> None:
    rendered = _all_rendered_text(running_app)
    for prefix in ("EVID_", "ACCT_", "LINE_", "CALL_"):
        for line in rendered.splitlines():
            if prefix in line:
                for token in line.replace("`", " ").split():
                    if token.startswith(prefix):
                        assert app.is_masked_identifier(token.strip(".,:")), token


def test_blocked_candidates_are_never_rendered_even_when_present(
    running_app: AppTest, tmp_path: Path
) -> None:
    """NEVER rendered - not greyed out, absent. Uses a fixture copy with a
    synthetic blocked candidate, loaded by the REAL running app (not just
    the pure view-model layer), to prove the renderer has no code path
    that touches it - not just that the shipped fixture happens to have
    none."""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    data["recommendation_set"]["blocked_candidates"] = [
        {
            "candidate_id": "C_SHOULD_NEVER_APPEAR",
            "reason": "PRO-007: competitor price match requires regional_manager tier",
            "governing_rules": ["PRO-007"],
        }
    ]
    tampered_path = tmp_path / "tampered_fixture.json"
    tampered_path.write_text(json.dumps(data), encoding="utf-8")

    running_app.get_by_key("fixture_path_input").set_value(str(tampered_path)).run()

    assert len(running_app.exception) == 0
    assert len(running_app.button) == 3  # still only the 3 real recommendations
    rendered = _all_rendered_text(running_app)
    assert "C_SHOULD_NEVER_APPEAR" not in rendered
    assert "PRO-007" not in rendered
