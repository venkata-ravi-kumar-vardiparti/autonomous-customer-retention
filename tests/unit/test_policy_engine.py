"""End-to-end engine behaviour: the exact reference scenario from the phase brief."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from churnguard.policy import engine, loader
from tests.unit.policy_fixtures import make_reference_request

FIXED_NOW = datetime(2026, 9, 19, tzinfo=UTC)


def test_reference_scenario_reproduces_exactly() -> None:
    request = make_reference_request()
    result = engine.evaluate(request, now=FIXED_NOW)

    by_id = {verdict.candidate_id: verdict for verdict in result.verdicts}

    c1 = by_id["C1"]
    assert c1.verdict == "pass"
    assert c1.governing_rules == ["RET-014", "BIL-003"]
    assert c1.approval_tier_required == 1
    assert c1.constraint_violations == []

    c2 = by_id["C2"]
    assert c2.verdict == "pass_with_disclosure"
    assert c2.governing_rules == ["PLN-021"]
    assert c2.approval_tier_required == 1
    assert len(c2.required_disclosures) == 1
    assert c2.required_disclosures[0].code == "DISC_HOTSPOT_REDUCTION"
    assert c2.required_disclosures[0].must_be_read_verbatim is True

    c3 = by_id["C3"]
    assert c3.verdict == "pass"
    assert c3.governing_rules == ["RET-014", "FIN-009"]
    assert c3.approval_tier_required == 2

    c4 = by_id["C4"]
    assert c4.verdict == "blocked"
    assert c4.governing_rules == ["PRO-007", "RET-002"]
    assert c4.approval_tier_required is None
    assert len(c4.constraint_violations) == 2

    assert result.prohibited_actions_triggered == ["PRO-007"]
    assert result.evaluation_mode == "deterministic"
    assert result.policy_pack_hash
    assert result.policy_pack_version == "2026.09.1"
    assert result.evaluated_at == FIXED_NOW


def test_computed_limits_reflect_agent_tier() -> None:
    tier1_result = engine.evaluate(make_reference_request(tier=1), now=FIXED_NOW)
    assert tier1_result.computed_limits.max_monthly_credit == pytest.approx(25.00)
    assert tier1_result.computed_limits.agent_authority_ceiling == 1

    tier2_result = engine.evaluate(make_reference_request(tier=2), now=FIXED_NOW)
    assert tier2_result.computed_limits.max_monthly_credit == pytest.approx(50.00)
    assert tier2_result.computed_limits.agent_authority_ceiling == 2

    assert tier1_result.computed_limits.max_discount_pct == pytest.approx(0.20)


def test_unknown_policy_pack_version_raises() -> None:
    request = make_reference_request()
    request = request.model_copy(update={"policy_pack_version": "9999.01.1"})
    with pytest.raises(loader.PolicyPackError):
        engine.evaluate(request, now=FIXED_NOW)


def test_evaluate_with_pack_rejects_mismatched_version() -> None:
    request = make_reference_request()
    pack = loader.get_pack("2026.09.1")
    mismatched_request = request.model_copy(update={"policy_pack_version": "0000.00.0"})
    with pytest.raises(ValueError, match="does not match"):
        engine.evaluate_with_pack(mismatched_request, pack, now=FIXED_NOW)


def test_engine_module_has_no_llm_or_data_imports() -> None:
    import ast
    import inspect

    for module in (engine, loader):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "churnguard.data" not in node.module
                assert "openai" not in node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "churnguard.data" not in alias.name
                    assert "openai" not in alias.name
