# Geppetto 2 — Run the Real-Time System

End-to-end runbook for the live Meeting Truth Layer. Runs locally on Windows; all
processing is local except the Whisper + Claude API calls.

## 1. Install

```bash
py -m pip install fastapi "uvicorn[standard]" openai anthropic chromadb python-dotenv pyaudio
```

(`.env` already holds `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`. The KB in
`chroma_data/` is already built.)

## 2. Start the server

```bash
uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in your browser. You'll see the history side panel
(left) and the live-alerts pane (center).

## 3. Start a live meeting

1. Click **“Start live meeting.”** The dashboard creates a session and shows the
   exact streamer command to run.
2. In a second terminal, run that command (it includes the session id):

   ```bash
   py phase1_audio_streaming.py --server http://127.0.0.1:8000 --session <id>
   ```

3. Make sure meeting audio is routed through VB-Cable (set **CABLE Input** as the
   default playback device; see `record_to_wav.py` header for the “listen” trick
   so you still hear it).

Speak or play audio. Within a few seconds of a factual claim, an alert card
appears in the center pane, color-coded, with the source and a suggested response.

## 4. End the meeting

Click **“End meeting.”** The server finalizes the transcript, builds the report,
saves it to `meetings/<timestamp>/` (transcript.txt + report.json + report.html),
and the meeting appears in the history panel automatically. Click it to review;
the ✕ deletes it.

## Component map

| Piece | File |
|---|---|
| Audio capture + streaming | `phase1_audio_streaming.py` |
| Server + dashboard + endpoints | `phase3_server_realtime.py` |
| Per-session transcribe → detect → validate | `phase3_session.py` |
| Incremental claim detection | `phase3_claims.py` |
| WebSocket push + reconnect replay | `phase3_websocket.py` |
| Claim validation (reused from MVP) | `phase2_validator.py` |
| Report build + storage (reused) | `phase3_integration.py`, `phase3_storage.py` |

## Acceptance checklist (REQUIREMENTS_REALTIME.md §9)

Run these once the server + streamer are up:

- **AC-1** Speak a scripted contradiction (e.g. "QA is 100% done" against a KB that
  says 82%). A **CONTRADICTED** alert should appear within ~12s, citing the source.
- **AC-2** Feed claims that exercise all five categories; each shows with evidence
  and a High/Medium/Low confidence label.
- **AC-3** Alerts appear live with no page refresh; the history panel stays usable.
- **AC-4** End the session → a `meetings/<timestamp>/` folder is written and the
  meeting auto-appears in history.
- **AC-5** Open and delete a saved meeting from the panel.
- **AC-6** Refresh the dashboard tab mid-session → it reconnects and the prior
  alerts are replayed (WebSocket history replay).
- **AC-7** Kill your network for one chunk (or stop/start) → you get a "transcription
  hiccup" warning and the session continues on the next chunk.
- **AC-8** Confirm logistics/greetings ("let's take a break") produce no alert,
  while factual claims do.
- **AC-9** Run ~10 minutes and confirm cost stays in budget (≈ Whisper minutes ×
  your rate; see the cost note in REQUIREMENTS §6.2).

## Notes / known limitations

- **STT = whisper-1, no priming** (spike showed priming caused hallucination loops,
  and gpt-4o models corrupted numbers like "6 to 8" → "628"). See the STT decision
  in project memory.
- Server-side **silence gating** is a backstop; the streamer does the real VAD.
- **NFR-11**: the in-progress transcript is flushed to `meetings/.live_<id>.txt`
  and removed on clean end — recoverable if the server is killed mid-session.
- Single active PM assumed (no auth / multi-tenant) — matches the non-goals.
```
