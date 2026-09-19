"""Every fixture transcript must parse as a valid ConversationInput."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from churnguard.contracts.conversation import ConversationInput

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "transcripts"
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))


def test_twelve_fixtures_present() -> None:
    assert len(FIXTURE_PATHS) == 12


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.stem)
def test_fixture_parses_as_conversation_input(path: Path) -> None:
    raw = json.loads(path.read_text())
    conversation_input = ConversationInput.model_validate({**raw, "prior_signals": None})
    assert conversation_input.call_id
    assert conversation_input.transcript_window
