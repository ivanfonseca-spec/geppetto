# Geppetto 3 — Temporal Truth Layer (Design)

**Status:** Built and operational
**Predecessor:** Geppetto 2 (real-time Meeting Truth Layer — stable, carried forward)
**Owner:** Ivan Fonseca
**Theme:** Make the knowledge base understand that facts change over time, and auto-update from your project documents.

---

## 1. The problem

Geppetto 2 treats the knowledge base as a single, current snapshot. It answers
"does this claim match what the KB says *right now*?" — and it does that well.

But real project facts **evolve**: QA progress goes 15% → 23% → 34% → 82%; a
deadline slips; a decision gets reversed. Geppetto 2 has no concept of this:

- **The KB is static.** It only knows what was last ingested. If progress moved
  to 23% but the KB still says 15%, a correct statement is flagged CONTRADICTED — a false alarm.
- **No freshness data.** Document metadata had no date, so the OUTDATED category
  existed but was unreliable.
- **Manual updates only.** There was no way to feed project documents and have the
  system learn from them automatically.

Geppetto 3's job: manage facts **as a timeline**, auto-ingest documents, and judge
claims against the *current* truth with provenance and freshness awareness.

---

## 2. Architecture overview

```
 ┌─────────────────────────────────────────────────────────┐
 │                    docs/  directory                      │
 │  .md  .txt  .pdf  .docx  (authoritative)                │
 │  docs/notes/  (derived — meeting notes)                  │
 └──────────────────┬──────────────────────────────────────┘
                    │  kb_sync (incremental, on startup + /api/sync)
          ┌─────────▼─────────┐        ┌─────────────────┐
          │  ChromaDB          │        │  SQLite          │
          │  (prose chunks)    │        │  facts store     │
          │  semantic search   │        │  versioned       │
          └─────────┬─────────┘        │  timeline        │
                    │                  └────────┬─────────┘
                    └──────────┬───────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Validator           │
                    │  temporal path first │
                    │  prose fallback      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Dashboard           │
                    │  Live alerts         │
                    │  KB / Pending tab    │
                    └─────────────────────┘
```

---

## 3. Two-layer knowledge store

| Layer | Technology | Purpose |
|---|---|---|
| Prose context | ChromaDB (vector store) | Semantic background — SOW, ADRs, transcripts |
| Versioned facts | SQLite (`facts` table) | Dated metric timeline — what changed and when |

Chroma answers "what does the SOW say about the database." SQLite answers "what is the current QA number and when was it last updated." Both are needed.

---

## 4. Document ingest (kb_sync)

`kb_sync.py` watches the `docs/` directory and runs an incremental sync:

- **On startup** (blocking, before serving requests)
- **Manually** via `POST /api/sync` or the Sync button in the dashboard
- **Incremental** — tracks a `.geppetto_manifest.json`; only new or changed files are re-processed
- **Two provenance tiers:**
  - `docs/` (root and subdirs except `notes/`) → **authoritative** — committed immediately as provisional facts
  - `docs/notes/` → **derived** (auto-generated meeting notes) — held in the pending queue until PM accepts

Supported file types: `.md`, `.txt`, `.pdf` (pymupdf), `.docx` (python-docx). Excel optional via `ENABLE_XLSX=1`.

---

## 5. Auto-discovered metric catalog

Metrics are not hardcoded. On each sync, Haiku extracts `{metric_key, value, unit, as_of}` tuples from each document. Known keys are injected into the extraction prompt on every pass to keep key naming stable across syncs. Keys follow `domain.attribute` convention (e.g., `qa.tests_passed_pct`, `budget.total`).

A small seed catalog of common keys (`facts.py: METRIC_CATALOG`) provides baseline stability and phrase hints for the claim tagger.

---

## 6. Human-in-the-loop review (Pending tab)

Auto-extracted facts are never committed blindly:

| Source tier | What happens | Confidence |
|---|---|---|
| Authoritative doc | Committed immediately as **provisional** | Capped at 0.60 for numeric until PM confirms |
| Derived (meeting notes) | Held in **pending queue** — not used until PM accepts | N/A until accepted |

The PM reviews both in the **Pending tab** of the dashboard:
- **Provisional facts** (from authoritative docs): Confirm / Reject
- **Pending extractions** (from meeting notes): Accept / Reject

Rejected items are remembered — they won't re-surface on the next sync.

---

## 7. Validation flow (freshness-aware)

For each detected claim (Option B pipeline — merged into one Haiku call):

1. **Detect + tag** — one Haiku call returns claim text AND metric_key/value/unit
2. **Resolve against the timeline:**
   - matches the **latest** version → **VERIFIED**
   - matches a **superseded** version → **OUTDATED** — current value surfaced to PM
   - conflicts with latest, no historical match → **CONTRADICTED**
   - no metric match → **UNVERIFIED** (falls back to prose KB via ChromaDB)
3. **Confidence caps:**
   - Provisional numeric fact → max 0.60 until confirmed
   - Stale fact (>7 days old) → max 0.55
4. **Concurrent validation** — multiple claims in one chunk are validated in parallel (asyncio.gather), not sequentially

---

## 8. Performance (Option A + B)

| Stage | Before | After A+B |
|---|---|---|
| Chunk fill | 4–6s | 2.5–4s |
| STT (Whisper) | 1–2s | 1–2s (async, non-blocking) |
| Detect + tag | 0.7s + 0.7s (2 calls) | 0.9s (1 merged call) |
| Validate N claims | N × 0.7s sequential | 0.7s concurrent (parallel) |
| **Total lag** | **6–12s** | **~3–5s** |

---

## 9. End-of-meeting notes

When a session ends, Haiku automatically generates structured meeting notes saved to two places:

- `docs/notes/YYYY-MM-DD-<slug>.md` — ingested as derived tier on next sync
- `meetings/<folder>/notes.md` — accessible from meeting history

Note format: Current Status / Key Achievements / Upcoming Priorities / Risks & Issues / Decisions & Support Needed / Action Items.

---

## 10. What carries over from Geppetto 2

| Component | Change in Geppetto 3 |
|---|---|
| Audio streamer | Reused; now auto-launched by server on session start |
| Whisper STT | Unchanged (whisper-1, no priming) |
| WebSocket / dashboard | Extended with KB panel, Pending tab, sync button |
| Incremental claim detector | Extended — now returns `Claim` objects with pre-tagged metrics |
| Validator | Extended — freshness-aware, accepts pre-supplied tags |
| Storage / history / reports | Reused; reports gain "current vs. as-stated" context |

---

## 11. Constraints (unchanged from Geppetto 2)

- STT: **whisper-1 only** — gpt-4o family corrupts numbers ("6 to 8" → "628")
- Local-first, Windows, no GPU
- Cloud calls: OpenAI (Whisper STT) + Anthropic (Haiku for all LLM tasks)
- API keys in `.env`, never committed to git
