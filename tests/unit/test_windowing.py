"""orchestration/windowing.py: next_window slicing and state advancement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from churnguard.contracts.conversation import ConversationSignals, TranscriptTurn
from churnguard.orchestration.windowing import WindowState, advance, next_window

_START = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _turn(offset_seconds: int, text: str = "hi") -> TranscriptTurn:
    return TranscriptTurn(
        ts=_START + timedelta(seconds=offset_seconds), speaker="customer", text=text
    )


def _signals(sentiment: str = "neutral") -> ConversationSignals:
    return ConversationSignals(
        intents=[],
        churn_signals=[],
        competitor_claims=[],
        customer_stated_figures=[],
        unresolved_concerns=[],
        sentiment_trajectory=sentiment,
        verification_tasks=[],
    )


def test_first_window_contains_every_turn_so_far() -> None:
    transcript = [_turn(0), _turn(10), _turn(20)]
    state = WindowState()

    conversation_input = next_window(transcript, state, call_id="CALL_****0001", locale="en-US")

    assert len(conversation_input.transcript_window) == 3
    assert conversation_input.prior_signals is None
    assert conversation_input.window_start_ts == transcript[0].ts


def test_next_window_contains_only_turns_since_the_last_processed_point() -> None:
    transcript = [_turn(0), _turn(10), _turn(20), _turn(30)]
    state = WindowState(processed_turn_count=2, prior_signals=_signals())

    conversation_input = next_window(transcript, state, call_id="CALL_****0001", locale="en-US")

    assert len(conversation_input.transcript_window) == 2
    assert conversation_input.transcript_window[0].ts == transcript[2].ts
    assert conversation_input.prior_signals is not None


def test_next_window_raises_when_there_is_nothing_new() -> None:
    transcript = [_turn(0), _turn(10)]
    state = WindowState(processed_turn_count=2)

    with pytest.raises(ValueError, match="no new turns"):
        next_window(transcript, state, call_id="CALL_****0001", locale="en-US")


def test_advance_records_the_new_processed_count_and_signals() -> None:
    signals = _signals("escalating")
    state = advance(WindowState(), full_transcript_len=5, signals=signals)

    assert state.processed_turn_count == 5
    assert state.prior_signals is signals


def test_windowing_stays_flat_across_many_turns() -> None:
    """Each successive window is bounded by turns-since-last-call, not call length."""
    state = WindowState()
    transcript: list[TranscriptTurn] = []

    for i in range(15):
        transcript.append(_turn(i * 10))
        conversation_input = next_window(transcript, state, call_id="CALL_****0001", locale="en-US")
        assert len(conversation_input.transcript_window) == 1
        state = advance(state, len(transcript), _signals())

    assert state.processed_turn_count == 15
