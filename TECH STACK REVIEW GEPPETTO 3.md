# TECH STACK REVIEW — GEPPETTO 3

**As-built review · June 2026**
**Status:** Fully operational hackathon prototype
**Owner:** Ivan Fonseca · Blend360

---

## Executive Summary

Geppetto 3 is a local-first, Windows-based real-time meeting fact-checker with a temporal truth layer. It listens to meeting audio, transcribes it with Whisper, detects factual claims, and validates them against a versioned knowledge base built from project documents. The system is fully operational as a hackathon prototype delivering ~3–5s end-to-end latency from spoken claim to dashboard alert.

---

## 1. Runtime Environment

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Python runtime | CPython | 3.12.x | PyAudio prebuilt wheels available; 3.14 has none |
| OS | Windows 11 | — | Local-first constraint |
| GPU | None | — | All inference via cloud APIs |
| Package manager | pip | — | Standard; `--break-system-packages` flag used in sandbox |

**Critical constraint:** Python must stay on 3.12 until PyAudio publishes 3.14 wheels. Do not upgrade Python to 3.13/3.14 without testing PyAudio installation first.

---

## 2. Backend Framework

| Component | Technology | Notes |
|---|---|---|
| API server | FastAPI | REST + WebSocket in one framework |
| ASGI server | Uvicorn | `py -3.12 -m uvicorn phase3_server_realtime:app` |
| Async runtime | Python asyncio | `ingest_chunk()` and `finalize()` are both async (Option B) |
| Thread pool | `asyncio.to_thread` | STT and validation run in threads; event loop never blocks |
| Concurrency guard | `threading.Lock` | Protects `self.alerts` and `self.claim_count` in thread pool context |

**Why `threading.Lock` not `asyncio.Lock`:** `_validate_one` runs inside `asyncio.to_thread` (a thread pool), not on the event loop. `asyncio.Lock` would deadlock. `threading.Lock` is correct here.

**Option B optimization:** Session chunk processing is fully async. Multiple claims in one audio chunk are validated concurrently via `asyncio.gather()`. Multi-claim validation collapses from N×0.7s to ~0.7s regardless of claim count.

---

## 3. AI / LLM Stack

| Task | Model | Provider | Notes |
|---|---|---|---|
| Speech-to-text | `whisper-1` | OpenAI | No priming; no gpt-4o family (corrupts numbers) |
| Claim detection + metric tagging | `claude-haiku-4-5-20251001` | Anthropic | Merged into ONE call per chunk (Option B) |
| Metric extraction from docs | `claude-haiku-4-5-20251001` | Anthropic | Runs during `kb_sync` |
| Claim validation — prose path | `claude-haiku-4-5-20251001` | Anthropic | ChromaDB fallback when no metric match |
| Meeting notes generation | `claude-haiku-4-5-20251001` | Anthropic | Two calls on session end (notes + slug) |

**Hard constraint — whisper-1 only:** The `gpt-4o` Whisper variant corrupts numeric transcriptions ("six to eight" → "628"), making fact-checking of numeric claims unreliable. `whisper-1` is the only permitted STT model.

**Option B — merged detect + tag:** Claim detection and metric tagging were previously two separate Haiku calls per chunk. Now merged into one call that returns both claim text and `{metric_key, value, unit}` per claim. The pre-supplied tag is passed directly to `validate_claim(tag=claim.tag)`, bypassing the internal `tag_metric()` call entirely. Saves one Haiku round-trip (~0.7s) per chunk.

---

## 4. Storage Layer

### 4.1 SQLite — Versioned Fact Store (`facts.db`)

Primary structured store. Manages the metric timeline.

**Tables:**

- `facts` — versioned metric records
  - Columns: `fact_id, metric_key, entity, value, unit, as_of, source, ingested_at, supersedes, provisional, confirmed_at`
- `pending_facts` — derived-tier extractions awaiting PM review
  - Columns: `pending_id, metric_key, value, unit, as_of, source, entity, tier, extracted_at, status`

**Key design decisions:**
- Append-only timeline — each update adds a row, never overwrites. `current()` = row with max `as_of` per `metric_key`.
- `provisional=True` — auto-extracted facts used immediately at reduced confidence (capped 0.60 for numeric) until PM confirms.
- Rejection memory — `status='rejected'` in `pending_facts` prevents re-surfacing on re-sync.
- Idempotent append — dedup on `(metric_key, as_of, value, source)` prevents timeline inflation on re-sync.

### 4.2 ChromaDB — Prose Vector Store (`chroma_data/`)

Semantic retrieval for unstructured knowledge. Handles everything SQLite cannot: architecture decisions, process choices, ownership, qualitative context.

**Key design decisions:**
- Sentence-boundary-aware chunking (800 chars, 100-char overlap)
- Metadata per chunk: `{source, file_type, modified_date, chunk_index, tier}`
- Serves as the fallback validation path when no metric match is found in SQLite

**Why both stores?** ChromaDB answers "what does the SOW say about the database architecture." SQLite answers "what is the current QA percentage and when was it last updated." Neither can replace the other.

### 4.3 Flat Files — Meeting Storage (`meetings/`)

Each session saved as a folder:

```
meetings/
└── 2026-06-18_12-26-31/
    ├── transcript.txt
    ├── report.json
    ├── report.html
    └── notes.md
```

**Known limitation:** No write atomicity. Server crash mid-save produces a partial folder. SQLite-unified store (Proposal 1 in §8) resolves this.

### 4.4 Manifest File (`.geppetto_manifest.json`)

Per-file tracking for incremental sync: `{sha256, mtime, size, chunk_ids[], ingested_at, tier}`. Written atomically via `tempfile.mkstemp()` + `os.replace()` to prevent crash corruption.

---

## 5. Knowledge Base Sync (`kb_sync.py`)

**Trigger:** Blocking on server startup; manual via `POST /api/sync` or dashboard Sync button.

**Flow:**
1. Walk `docs/` recursively
2. Compute SHA256 + mtime per file; compare to manifest — skip unchanged, re-ingest modified, purge deleted
3. Load document via `doc_loaders.py`
4. Chunk text (sentence-boundary aware, 800 chars, 100-char overlap)
5. Embed chunks into ChromaDB
6. Extract metrics via Haiku → route by provenance tier:
   - **Authoritative** (`docs/` root and subdirs except `notes/`) → `add_fact(..., provisional=True)`
   - **Derived** (`docs/notes/`) → `add_pending(...)`
7. Write updated manifest atomically

**Supported file types:** `.md`, `.txt`, `.pdf` (pymupdf), `.docx` (python-docx). `.xlsx` optional via `ENABLE_XLSX=1` env flag.

---

## 6. Audio Pipeline

| Stage | Technology | Settings |
|---|---|---|
| Capture device | VB-Audio Virtual Cable | Loopback — captures all call participants |
| PM microphone | Jabra EVOLVE 20 | Set as Teams/Zoom mic input; VB-Cable receives its output |
| Sample rate | 16000 Hz | Whisper native rate |
| Format | WAV, mono, 16-bit PCM | — |
| Frame size | 30 ms | ~480 samples per frame |
| VAD | Rolling median RMS | `SILENCE_RATIO=0.3`, `MIN_ABS_RMS=50.0` |
| Chunk size | 2.5s min / 4.0s max | Reduced from 4.0s/6.0s (Option A) |
| Overlap | 0.5s | Prevents word truncation at chunk boundaries |

**VB-Cable setup (required for full call capture):** Route Teams/Zoom speaker output to `CABLE Input (VB-Audio Virtual Cable)`. Geppetto reads from VB-Cable — capturing all participants' audio. Jabra remains the microphone for the PM's voice into the call.

**Auto-launch:** Server spawns `phase1_audio_streaming.py` as a subprocess when `POST /api/session/start` is called. No manual streamer launch needed. Streamer terminates when `POST /api/session/{sid}/end` is called.

---

## 7. Frontend

| Component | Technology | Notes |
|---|---|---|
| Framework | Vanilla JavaScript | No React/Vue dependency |
| Styling | Custom CSS | No Tailwind/Bootstrap |
| Real-time updates | WebSocket | Server pushes alerts; no polling |
| Delivery | `dashboard.html` | Single self-contained file served by FastAPI |

**Dashboard panels:**
- **Live alerts** (center) — color-coded by verdict, newest first, temporal context inline
- **Meeting history** (left) — readable date format ("Jun 18 · 12:26"), colored claim pills, faded empty sessions
- **Knowledge Base panel** (right) — Metrics tab (current values + history), Pending tab (provisional + pending queue), Update tab (manual fact entry)
- **Session modal** — structured alert list by category (CONTRADICTED first), collapsible raw transcript via `<details>`

---

## 8. Validation Pipeline (as-built, Option A+B)

```
Audio chunk fills (2.5–4s)
  └─ VAD backstop: skip if RMS < SILENCE_RMS
  └─ asyncio.to_thread(transcribe_chunk)     [Whisper, 1–2s]
  └─ detector.feed_text(text)                [sentence buffer]
       └─ ONE Haiku call:                    [~0.9s]
            detect claims AND tag metric_key/value/unit per claim
       └─ asyncio.gather(                    [~0.7s, concurrent]
            _validate_one(claim1),
            _validate_one(claim2), ...
          )
            └─ validate_claim(claim, tag=claim.tag)
                 ├─ Temporal path (SQLite):
                 │    VERIFIED / OUTDATED / CONTRADICTED
                 └─ Prose fallback (ChromaDB + Haiku):
                      UNVERIFIED / NEEDS_CLARIFICATION
  └─ WebSocket push to dashboard
─────────────────────────────────────────────────────────
Total: ~3–5s from end-of-sentence to alert
```

---

## 9. Human-in-the-Loop Review

| Trigger | What happens | PM action |
|---|---|---|
| Authoritative doc synced | Fact committed as `provisional=True` | Confirm / Reject in Pending tab |
| Derived doc (meeting notes) synced | Fact held in `pending_facts` queue | Accept / Reject in Pending tab |
| PM confirms provisional | `provisional=0`, `confirmed_at` set; confidence cap lifted | — |
| PM rejects provisional | Row deleted from `facts` | — |
| PM accepts pending | Row moved to `facts` as non-provisional | — |
| PM rejects pending | `status='rejected'` — not re-surfaced on next sync | — |

---

## 10. Security

- API keys stored in `.env` only — never committed to git
- `.gitignore` covers: `.env`, `chroma_data/`, `facts.db`, `__pycache__/`, `*.pyc`, `.geppetto_manifest.json`, `meetings/`
- Git history scrubbed via `git filter-branch` after accidental `.env` commit
- GitHub Push Protection enforced — blocks secrets in commits
- All processing local; only external calls are OpenAI (Whisper) and Anthropic (Haiku)

---

## 11. Latency Profile (Before vs. After Optimization)

| Stage | Baseline | After Option A+B |
|---|---|---|
| Chunk fill | 4–6s | 2.5–4s |
| Whisper STT | 1–2s | 1–2s (async, non-blocking) |
| Detect + tag | 0.7s + 0.7s (2 calls) | 0.9s (1 merged call) |
| Validate N claims | N × 0.7s sequential | ~0.7s concurrent |
| **Total** | **6–12s** | **~3–5s** |

---

## 12. Storage Optimization Proposals (Post-hackathon)

| Proposal | Improvement | Feasibility | Summary |
|---|---|---|---|
| SQLite-unified store | 7/10 | 9/10 | Collapse flat-file meetings into a `meetings` table in `facts.db` — atomic writes, indexed queries, cross-meeting analytics |
| Event log architecture | 8/10 | 7/10 | Append-only event stream (chunk / claim / alert / ended) — fully replayable, crash-recoverable, no partial-folder risk |
| SharePoint sync | 9/10 | 6/10 | Post-save upload to SharePoint — team-accessible reports, KB pulls from shared document library |

---

## 13. Known Limitations (Post-hackathon backlog)

1. **Storage atomicity** — flat-file meeting save is not atomic; SQLite-unified store resolves this
2. **Single-user** — dashboard is local-only; SharePoint proposal addresses team access
3. **No speaker diarization** — cannot attribute claims to specific speakers
4. **Numeric STT fragility** — Whisper occasionally mishears numbers in very short (<2.5s) clips
5. **Python 3.12 pinned** — runtime must stay on 3.12 until PyAudio publishes 3.14 wheels
6. **XLSX deferred** — Excel support flag-gated behind `ENABLE_XLSX=1`
7. **Cross-chunk STT/validate overlap not implemented** — would require fixed 2s chunks; deferred per `OPTION_B_PLAN.md`

---

## 14. One-Command Launch

```batch
cd "Documents\Geppetto 3"
start.bat
```

Starts server → waits 4s → opens browser → user clicks "Start live meeting" → streamer auto-launches → VB-Cable captures all participants → alerts appear in ~3–5s.

---

## 15. File Reference

| File | Role |
|---|---|
| `phase1_audio_streaming.py` | Audio capture, VAD, chunking, streaming to server |
| `phase2_validator.py` | Freshness-aware validation engine (temporal + prose paths) |
| `phase3_claims.py` | Incremental claim detector; `Claim` dataclass; merged detect+tag (Option B) |
| `phase3_session.py` | Per-session async engine; concurrent validation; threading lock |
| `phase3_server_realtime.py` | FastAPI server; session lifecycle; WebSocket; streamer auto-launch |
| `phase3_integration.py` | KB setup; `MeetingValidator` orchestrator |
| `kb_sync.py` | Incremental doc ingest; provenance tier routing; manifest management |
| `facts.py` | SQLite fact store; provisional/pending queue; idempotent append |
| `fact_extractor.py` | Haiku-powered metric extraction from documents |
| `doc_loaders.py` | Multi-format loader (.md, .txt, .pdf, .docx) |
| `meeting_notes.py` | End-of-meeting Haiku-generated notes |
| `dashboard.html` | Single-file frontend |
| `start.bat` | One-command launcher |
| `facts.db` | SQLite fact store (gitignored) |
| `chroma_data/` | ChromaDB vector store (gitignored) |
| `docs/` | Project documents (authoritative tier) |
| `docs/notes/` | Auto-generated meeting notes (derived tier) |
| `meetings/` | Per-session transcripts, reports, notes (gitignored) |
| `.geppetto_manifest.json` | Incremental sync manifest (gitignored) |
