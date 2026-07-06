# Geppetto 3 — Requirements

**Status:** As-built · June 2026
**Owner:** Ivan Fonseca · Blend360

---

## Functional Requirements

### FR-1 · Real-time audio capture
The system shall capture meeting audio continuously during a live session. It shall support two capture modes: VB-Cable loopback (captures all call participants) and system default microphone (PM only). The capture mode shall be configurable via the `AUDIO_DEVICE` environment variable without code changes.

### FR-2 · Speech-to-text transcription
The system shall transcribe captured audio using OpenAI whisper-1. Transcription shall run asynchronously so it does not block audio capture. The system shall retry on transient API failures with exponential backoff (up to 2 retries).

### FR-3 · Claim detection
The system shall detect verifiable factual claims from transcribed speech. Only checkable assertions shall be extracted — status, percentages, dates, ownership, approvals, decisions, numeric facts. Opinions, questions, greetings, and filler shall be ignored. Duplicate or near-duplicate claims within a session shall be suppressed.

### FR-4 · Metric tagging (merged with FR-3)
Claim detection and metric tagging shall be performed in a single LLM call. Each detected claim shall carry a pre-tagged `{metric_key, value, unit}` where the claim maps to a known metric. Claims with no metric match shall carry a null tag.

### FR-5 · Freshness-aware validation
The system shall validate each claim against a versioned fact timeline:
- Claim matches the latest recorded value → **VERIFIED**
- Claim matches a superseded value → **OUTDATED** (current value and date surfaced)
- Claim conflicts with the latest value → **CONTRADICTED**
- No metric match → **UNVERIFIED** (prose fallback via ChromaDB)

### FR-6 · Concurrent validation
Multiple claims detected within one audio chunk shall be validated concurrently, not sequentially. Total validation time for N claims shall not exceed the time for a single claim.

### FR-7 · Real-time alerts
Validated claims shall be pushed to the PM's dashboard via WebSocket within ~3–5 seconds of the spoken statement. Alerts shall include: verdict, confidence level, evidence snippet, source document, suggested PM response, and temporal context (current value, as-of date) where applicable.

### FR-8 · Document knowledge base
The system shall maintain a two-layer knowledge base:
- **ChromaDB** — semantic vector store for prose context (SOW, ADRs, process docs)
- **SQLite** — versioned, append-only metric timeline

### FR-9 · Incremental document sync
The system shall sync the `docs/` directory into the knowledge base on every server startup and on demand via the dashboard Sync button. Only new or modified files shall be reprocessed. Deleted files shall be purged. Sync shall be idempotent — re-syncing unchanged files produces no change.

### FR-10 · Provenance tiers
Documents shall be classified into two tiers:
- **Authoritative** — `docs/` root and subdirectories except `notes/`
- **Derived** — `docs/notes/` (auto-generated meeting notes)

Authoritative sources shall take precedence over derived sources in validation. A derived note shall never override an authoritative fact.

### FR-11 · Human-in-the-loop review
Auto-extracted facts shall never be granted full trust automatically:
- Authoritative extractions shall be committed as **provisional** — used immediately at reduced confidence, flagged as unconfirmed in alerts
- Derived extractions shall be held in a **pending queue** — not used in validation until the PM explicitly accepts them
- The PM shall be able to Confirm, Reject (authoritative) or Accept, Reject (derived) each item from the dashboard Pending tab
- Rejected items shall be remembered and not re-surfaced on subsequent syncs

### FR-12 · Confidence caps
- Provisional numeric facts shall be capped at 0.60 confidence until PM confirms
- Facts not updated in more than 7 days shall be capped at 0.55 confidence and flagged as stale

### FR-13 · End-of-meeting notes
When a session ends, the system shall automatically generate structured meeting notes via Haiku and save them to:
- `docs/notes/YYYY-MM-DD-<slug>.md` (derived tier — available as context on next sync)
- `meetings/<folder>/notes.md` (permanent record)

Note format: Current Status · Key Achievements · Upcoming Priorities · Risks & Issues · Decisions & Support Needed · Action Items.

### FR-14 · Meeting history
The system shall save a complete record for every session: transcript, JSON report, HTML report, and meeting notes. Past sessions shall be accessible from the dashboard history panel.

### FR-15 · One-command launch
The system shall start with a single command (`start.bat`). The audio streamer shall launch automatically when the PM clicks "Start live meeting" — no separate manual step required. The streamer shall terminate automatically when the session ends.

---

## Non-Functional Requirements

### NFR-1 · Latency
End-to-end latency from spoken statement to dashboard alert shall be ≤ 5 seconds under normal conditions.

### NFR-2 · STT accuracy for numbers
The system shall use whisper-1 exclusively for speech-to-text. The gpt-4o Whisper variant is prohibited — it corrupts numeric transcriptions (e.g. "six to eight" → "628"), making numeric fact-checking unreliable.

### NFR-3 · Local-first
All data shall be stored locally. No meeting audio, transcripts, or alerts shall be transmitted to external systems. The only external calls permitted are OpenAI (Whisper STT) and Anthropic (Haiku LLM).

### NFR-4 · No GPU required
The system shall run on a standard Windows laptop without a dedicated GPU. All inference is via cloud APIs.

### NFR-5 · API key security
API keys shall be stored in `.env` only and never committed to version control. `.env` shall be covered by `.gitignore`. GitHub Push Protection shall be enforced on the repository.

### NFR-6 · Idempotent sync
Re-syncing the same document shall not create duplicate entries in the fact store or ChromaDB. Deduplication shall be on `(metric_key, as_of, value, source)`.

### NFR-7 · Atomic manifest writes
The sync manifest (`.geppetto_manifest.json`) shall be written atomically via temp file + `os.replace()` to prevent corruption on crash.

### NFR-8 · Async non-blocking server
The FastAPI server shall not block the event loop during STT or LLM calls. All blocking operations shall run via `asyncio.to_thread`.

### NFR-9 · Thread-safe shared state
Shared session state (`alerts`, `claim_count`) mutated from concurrent validation threads shall be protected by `threading.Lock`.

### NFR-10 · Transient failure resilience
Whisper STT failures shall be retried up to 2 times with exponential backoff. A persistent failure on a single chunk shall log a warning and continue the session — it shall not terminate the session.

### NFR-11 · Python 3.12
The system shall run on Python 3.12. Python 3.13/3.14 is not supported — PyAudio prebuilt wheels are not available for those versions.

---

## Constraints

| Constraint | Value |
|---|---|
| STT model | whisper-1 only |
| LLM model | claude-haiku-4-5-20251001 (all LLM tasks) |
| OS | Windows 11 |
| Python | 3.12.x |
| GPU | None — cloud inference only |
| External services | OpenAI, Anthropic |
| Storage | Local only |
| Users | Single PM (no multi-tenant) |
| Auth | None (local tool) |

---

## Out of Scope (current version)

- Speaker diarization (attributing claims to specific speakers)
- Multi-user / shared dashboard
- Excel (.xlsx) ingest — flag-gated behind `ENABLE_XLSX=1`, deferred
- Meeting write-back to authoritative KB without PM confirmation
- Cross-chunk STT/validate overlap (deferred per OPTION_B_PLAN.md)
- Mobile or web deployment
