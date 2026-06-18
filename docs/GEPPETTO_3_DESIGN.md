# Geppetto 3 — Temporal Truth (Design)

**Status:** Design draft
**Predecessor:** Geppetto 2 (real-time Meeting Truth Layer — this works and is stable)
**Owner:** Ivan Fonseca
**Theme:** Make the knowledge base understand that facts change over time.

---

## 1. The problem

Geppetto 2 treats the knowledge base as a single, current snapshot. It answers
"does this claim match what the KB says *right now*?" — and it does that well.

But real project facts **evolve**: QA progress goes 15% → 23% → 34% → 82%; a
deadline slips from June 21 to June 28; a decision gets reversed. Geppetto 2 has
no concept of this:

- **The KB is static.** It only knows what was last ingested. If progress moved
  to 23% but the KB still says 15%, a *correct* statement ("QA is 23%") is flagged
  CONTRADICTED — a false alarm. The tool is only ever as current as its last update.
- **No freshness data.** Document metadata is just `{source, chunk, type}` — there
  is no date. So even though an OUTDATED category exists, the system can't reliably
  tell which of two values is newer, or resolve conflicts between a stale source and
  a current one. (This is the NFR-8 gap flagged but never built in Geppetto 2.)

Geppetto 3's job: manage facts **as a timeline**, so claims are judged against the
*current* truth, superseded values are recognized as OUTDATED (not wrong), and the
KB can advance over time without losing history.

---

## 2. Core idea — separate prose from facts

Keep two layers of knowledge:

1. **Documents (prose context)** — as in Geppetto 2: SOW, ADRs, transcripts,
   indexed in a vector store for semantic background.
2. **Facts (structured, versioned)** — a new, dated time-series of canonical
   metrics. Each fact is a key → value with an `as_of` date and a source. Updates
   **append a new version** rather than overwrite, so history is preserved.

Example fact timeline for one metric:

```
metric_key: qa.tests_passed_pct
  { value: 15, as_of: 2026-06-08, source: standup_notes }
  { value: 23, as_of: 2026-06-11, source: qa_status_report }
  { value: 34, as_of: 2026-06-13, source: qa_status_report }
  { value: 82, as_of: 2026-06-14, source: qa_status_report }   <- current
```

The "current truth" for a metric is simply the version with the **latest `as_of`**.
Older versions are retained as history and are what make OUTDATED detection possible.

---

## 3. Data model

**Fact record**

```json
{
  "fact_id": "uuid",
  "metric_key": "qa.tests_passed_pct",   // canonical, dotted
  "entity": "QA",                          // human label
  "value": 82,
  "unit": "percent",                       // percent | date | bool | money | text
  "as_of": "2026-06-14",                   // when the fact was true
  "source": "qa_status_report.md",
  "ingested_at": "2026-06-15T09:00:00Z",
  "supersedes": "fact_id-of-prev"          // optional link to prior version
}
```

**Store.** A small local **SQLite** time-series table (`facts`) sits alongside the
existing Chroma vector store. Chroma still handles prose retrieval; SQLite handles
the versioned numeric/dated truth. (Postgres + pgvector remains the future scale
option, as noted in the Geppetto 2 spec.)

**Why both:** prose retrieval is good at "what does the SOW say about the database,"
but it's bad at "what is the single most recent QA number." A structured fact store
answers the second precisely and cheaply, and gives us reliable dates.

---

## 4. Validation flow (freshness-aware)

For each detected claim:

1. **Detect** the claim (unchanged from Geppetto 2).
2. **Link** it to a metric — map "QA is at 80%" → `qa.tests_passed_pct`. Done by a
   lightweight LLM tagging step (claim → metric_key + extracted value/unit), backed
   by the list of known metric keys. Falls back to prose retrieval if no metric matches.
3. **Resolve against the timeline:**
   - matches the **latest** version → **VERIFIED**
   - matches a **superseded** (older) version → **OUTDATED** — and we tell the PM the
     current value ("that was true on June 8; it's 82% as of June 14")
   - conflicts with the latest and matches no version → **CONTRADICTED**
   - no matching metric/fact → **UNVERIFIED** (fall back to prose KB)
   - partial / ambiguous / temporal → **NEEDS_CLARIFICATION**
4. **Confidence (NFR-6)** computed, not guessed, from: recency of the matching fact,
   agreement across sources, and retrieval/match score.

This finally makes OUTDATED a first-class, reliable outcome instead of an accident
of what the retriever surfaced.

---

## 5. Freshness rules (NFR-8, made concrete)

- **Current = newest `as_of`** per metric_key.
- **Staleness guard:** if the newest fact for a metric is older than a configurable
  window (e.g., 7 days), drop confidence to Low and surface "this metric may be
  stale (last updated June 8)" rather than asserting VERIFIED/CONTRADICTED.
- **Source precedence:** when two sources report the same metric for the same date,
  use a per-metric precedence list (e.g., `qa_status_report` outranks `standup_notes`).
- **No fact ≠ wrong:** absence of a fact is UNVERIFIED, never CONTRADICTED.

---

## 6. How facts get updated (the part Geppetto 2 deferred)

Three options, in increasing ambition — Geppetto 3 should start with the first two:

1. **`update_fact()` API + dashboard affordance** — explicit: "QA is now 90% as of
   today." Appends a version. Simple, safe, auditable.
2. **Document re-ingestion with fact extraction** — when an updated doc is added, a
   fact-extraction pass pulls dated metrics out of it automatically.
3. **Meeting write-back (human-in-the-loop)** — if a meeting says "QA is now 90%,"
   offer the PM a one-click "update the KB to 90%?" This is the write-back workflow
   Geppetto 2 listed as a non-goal; powerful but must be **confirmed**, never automatic,
   with an audit trail.

---

## 7. What carries over from Geppetto 2 (reuse, don't rebuild)

| Component | Change in Geppetto 3 |
|---|---|
| Audio streamer, transcription, WebSocket, dashboard | **Reuse as-is** |
| Incremental claim detector | Reuse + add a metric-tagging step |
| Validator | **Extend**: timeline/freshness-aware resolution |
| Knowledge base | **Add** structured `facts` store (SQLite) + `as_of` dates; keep Chroma for prose |
| Storage / history / reports | Reuse; reports gain "current vs. as-stated" context |

The real-time machinery is done. Geppetto 3 is mostly a **KB + validator** evolution.

---

## 8. Open design decisions (to settle before/while building)

1. **Metric catalog** — hand-define the metric keys (qa %, release date, db choice,
   budget…) up front, or auto-discover them from documents? (Lean: seed a small
   hand-defined catalog, allow growth.)
2. **Write-back trust** — do we let meetings update the KB at all (option 3), and if
   so, always human-confirmed? (Lean: yes, confirmed-only, with audit log.)
3. **Conflicting current sources** — when SOW and QA report disagree *today*,
   precedence list vs. surfacing both to the PM. (Lean: precedence + show the conflict.)
4. **Numeric fragility** — transcribed numbers are unreliable (Geppetto 2 STT
   finding: "6 to 8" → "628"). Confidence should reflect transcription uncertainty for
   numeric claims, and maybe ask for confirmation on high-stakes numbers.

---

## 9. Acceptance shape (what "done" looks like)

- Feeding the same metric at increasing values over time and asking about each:
  the latest reads VERIFIED, an older value reads OUTDATED **with the current value
  surfaced**, and a never-seen value reads CONTRADICTED.
- A metric not updated in >7 days is flagged stale (Low confidence) rather than
  asserted.
- `update_fact()` advances a metric without rebuilding the whole KB, and the change
  is visible in the next validation.
- Everything Geppetto 2 did still works (real-time alerts, history, save/load).
