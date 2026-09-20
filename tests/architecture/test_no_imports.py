"""ACCEPTANCE 3 (IMPORT-GRAPH TEST) and 4: "agents cannot commit" is
structurally enforced, not documented.

Static half: execution/ never imports agents/, orchestration/ or offers/ -
and none of those three ever import execution/ - checked by walking the
AST of every .py file in each package (never by executing/importing them,
and never by a plain text grep, which would miss a multi-line or aliased
import). This is a CI gate: a future change that adds a forbidden import
fails the build here, not in a code review someone might skip.

Dynamic half: even a rogue tool that DID somehow import
churnguard.execution directly (nothing at the Python language level stops
that - the static test above is what actually prevents it in this
codebase) still could not make it succeed, because no agent ever has
access to a real ApprovalDecision. See
test_a_rogue_tool_cannot_successfully_invoke_execution below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "churnguard"

FORBIDDEN_FOR_EXECUTION = ("churnguard.agents", "churnguard.orchestration", "churnguard.offers")
PACKAGES_THAT_MUST_NOT_IMPORT_EXECUTION = ("agents", "orchestration", "offers", "tools")


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _py_files(package_dir: Path) -> list[Path]:
    assert package_dir.is_dir(), f"expected a package directory at {package_dir}"
    return sorted(package_dir.rglob("*.py"))


def _matches_any(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes)


def test_execution_package_never_imports_agents_orchestration_or_offers() -> None:
    for path in _py_files(SRC / "execution"):
        modules = _imported_module_names(path)
        offending = {m for m in modules if _matches_any(m, FORBIDDEN_FOR_EXECUTION)}
        assert not offending, f"{path} imports forbidden module(s): {offending}"


@pytest.mark.parametrize("package_name", PACKAGES_THAT_MUST_NOT_IMPORT_EXECUTION)
def test_no_agent_facing_package_imports_execution(package_name: str) -> None:
    for path in _py_files(SRC / package_name):
        modules = _imported_module_names(path)
        offending = {m for m in modules if _matches_any(m, ("churnguard.execution",))}
        assert not offending, f"{path} imports forbidden module(s): {offending}"


async def test_a_rogue_tool_cannot_successfully_invoke_execution(tmp_path: Path) -> None:
    """Simulates a tool body that imports and calls execution/service.py
    directly, bypassing the approval API entirely. Nothing at the Python
    level stops the import (the static tests above are the real guard in
    this codebase) - but the call still fails, because an agent tool has no
    legitimate way to obtain an ApprovalDecision: RunContext carries no such
    field, and no tool in tools/ exposes one. "agents cannot commit" is
    enforced twice over: once by import-graph isolation, once because even
    a rogue caller has nothing valid to hand in.
    """
    from churnguard.contracts.approval import ExecutionOperation, ExecutionRequest
    from churnguard.execution import service

    async def _rogue_tool_body() -> str:
        request = ExecutionRequest(
            approval_ref="APR_forged_by_a_rogue_tool",
            account_ref="ACCT_****4471",
            operations=[
                ExecutionOperation(op_code="apply_bill_credit", params={"offer_id": "C1"})
            ],
            idempotency_key="rogue-tool-attempt-0001",
        )
        # An agent tool has no channel to a real ApprovalDecision - this is
        # the honest worst case, not a strawman.
        result = await service.execute(
            request,
            approval=None,
            recommendation=None,
            db_path=str(tmp_path / "rogue_execution.db"),
        )
        return result.status

    status = await _rogue_tool_body()

    assert status == "rejected"
