"""Transcript windowing: keeps the Conversation agent's input flat on a long
call.

Each call to the agent sends only the turns since the last one processed,
plus prior_signals (the previous window's structured output) - never the
whole call history. ConversationInput.transcript_window already models
"a window", not "the transcript so far" (contracts/conversation.py), so the
only thing this module adds is the bookkeeping of where the last window
left off, and building the next one from it.
"""

from __future__ import annotations

from dataclasses import dataclass

from churnguard.contracts.conversation import (
    ConversationInput,
    ConversationSignals,
    TranscriptTurn,
)


@dataclass(frozen=True)
class WindowState:
    """How much of the call has been processed so far, and what it produced.

    processed_turn_count is a count, not an index into a list the state
    itself holds - the caller owns the growing transcript; this only tracks
    where the last window's slice ended.
    """

    processed_turn_count: int = 0
    prior_signals: ConversationSignals | None = None


def next_window(
    full_transcript: list[TranscriptTurn],
    state: WindowState,
    *,
    call_id: str,
    locale: str,
    guardrail_flags: list[str] | None = None,
) -> ConversationInput:
    """The next ConversationInput: only turns after state.processed_turn_count.

    Raises ValueError if there's nothing new to process - callers shouldn't
    invoke the agent again until at least one more turn has arrived.
    """
    new_turns = full_transcript[state.processed_turn_count :]
    if not new_turns:
        raise ValueError("no new turns since the last processed window")
    return ConversationInput(
        call_id=call_id,
        transcript_window=new_turns,
        window_start_ts=new_turns[0].ts,
        prior_signals=state.prior_signals,
        locale=locale,
        guardrail_flags=list(guardrail_flags or []),
    )


def advance(
    state: WindowState, full_transcript_len: int, signals: ConversationSignals
) -> WindowState:
    """The next WindowState, after the agent successfully processed a window.

    full_transcript_len is passed explicitly (not re-derived) so a caller
    that windows over a transcript still growing mid-call always advances
    to exactly the length it built the just-processed window from.
    """
    return WindowState(processed_turn_count=full_transcript_len, prior_signals=signals)


__all__ = ["WindowState", "advance", "next_window"]
