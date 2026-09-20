# ChurnGuard

Multi-agent retention decision-support prototype for a telecom contact
centre. See [CLAUDE.md](CLAUDE.md) for the full business context,
architecture, repo layout and phase plan.

```
make install
make test
```

## Agent Desktop UI (Streamlit)

```
streamlit run ui/agent_desktop.py
```

By default this renders entirely from the static fixture at
`fixtures/recommendation_sets/ref_call_88213.json` - **no backend needs to
be running**. Use the sidebar to change "Your approval tier" (try 1 vs 2 to
see offer C3 switch between "Escalate" and "Approve") or point "Fixture
path" at a different dump.

### Manual smoke script: the full live approve -> execute flow

The automated test suite (`tests/ui/test_disclosure_gating.py`) only
exercises the UI against the static fixture, by design (see CLAUDE.md's
Phase 9 notes - "build and test this entirely against a static fixture
JSON first"). `POST /calls/{id}/recommend` calls the real Supervisor
pipeline (Phase 7), which needs a real `OPENAI_API_KEY` and network access
neither this repo's test suite nor this sandbox has - so the full live
loop is a **manual** smoke test, run by a human with real credentials:

1. In one terminal, set a real key and start the API:
   ```
   export OPENAI_API_KEY=sk-...
   uvicorn churnguard.api.app:app --reload
   ```
2. In a second terminal, start the UI and turn on **Live mode** in the
   sidebar (API base URL defaults to `http://localhost:8000`):
   ```
   streamlit run ui/agent_desktop.py
   ```
3. Drive a call end-to-end:
   - `POST /calls/CALL_TEST0001/recommend` with a body shaped like
     `RecommendRequestBody` (see `src/churnguard/api/routes.py`) - the
     easiest way is `curl -X POST localhost:8000/calls/CALL_TEST0001/recommend -d @body.json -H "Content-Type: application/json"`,
     or the interactive docs at `http://localhost:8000/docs`.
   - Tick every checkbox under an offer card's required disclosures until
     its button becomes enabled, then click **Approve** (or **Escalate**,
     if the offer's tier exceeds your sidebar tier) - the UI calls
     `POST /approvals` then, for an approval, `POST /execute`, and shows
     the result inline.
   - Click **Approve** on the same offer again (or re-run the same
     `idempotency_key` via `curl`) and confirm the response comes back
     with `"replayed": true` and an unchanged result - never a second
     effect.
   - Try approving with a deliberately unmet disclosure or an
     insufficient tier and confirm `POST /execute` returns `422` with the
     matching rejection reason (see CLAUDE.md's Phase 8 notes for the
     five mandatory rejection paths).

Verified during Phase 9 development (see CLAUDE.md's Phase 9 acceptance
notes): the approve -> execute -> replay -> reject-unknown-approval loop,
driven through `ui/agent_desktop.py`'s own live-mode functions against a
real running FastAPI server, works exactly as above for every step that
doesn't require a live LLM call.
