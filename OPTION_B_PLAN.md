# Option B — Pipeline Latency (revised)

## Goal
Cut end-to-end latency without deferring alerts or losing error handling, by:
1. **Merging claim detection + metric tagging** into one Haiku call (removes a round-trip).
2. **Validating a chunk's claims concurrently** instead of one at a time.

> Revised from the first draft, which proposed overlapping STT(N) with validation
> of chunk **N-1**. That defers every alert by one chunk — and since chunks here are
> **4–6s VAD-aligned** (not 2s), it would have *increased* alert latency, not reduced
> it. See "Not doing now" below. The two wins kept here are unconditional and don't
> defer anything.

---

## Reality check: chunk size drives the math

The audio streamer cuts on natural pauses at **~4–6s** (`phase1_audio_streaming.py`,
MIN 4 / MAX 6). So the dominant latency term is the chunk filling, not the model
calls. Per-chunk processing (from the spike): whisper-1 ≈ 1.3s avg, Haiku ≈ 0.5–1s.

```
sequential today:  STT (1.3s) → detect (0.7s) → tag (0.7s) → validate ×N (0.7s each)
                   ≈ 2.7s + 0.7s×(claims) after the chunk closes
revised target:    STT (1.3s) → detect+tag (0.9s, one call) → validate ×N in parallel (0.7s)
                   ≈ ~3s after the chunk closes, regardless of claim count
```

Honest target: **~3–4s from end-of-sentence to alert** at 4–6s chunks. The savings
come from one fewer round-trip and from collapsing multi-claim validation — not from
cross-chunk tricks.

---

## Changes

### 1. `phase3_claims.py` — merge detect + tag into one call

**Current:** claim detection is one Haiku call; metric tagging is a second call in
`phase2_validator.py`.

**Change:** extend the detection prompt to return `metric_key`/`value`/`unit` per
claim in the same JSON.

```json
{
  "claims": [
    { "text": "QA is at 82%", "metric_key": "qa.tests_passed_pct", "value": "82", "unit": "percent" }
  ]
}
```

**Files:** `phase3_claims.py` (prompt + return shape).
**Risk:** Low — prompt-only; fall back to `metric_key: null` if absent.
**Watch:** spot-check that tagging quality holds when the prompt does detect+tag together.

### 2. `phase2_validator.py` — accept a pre-supplied tag

**Change:** add an optional `tag` param; skip the internal `tag_metric()` call when
the merged detect+tag already supplied one.

```python
def validate_claim(claim, kb_collection, db_path=None, tag=None):
    if tag is None:
        tag = tag_metric(claim)   # only call if not pre-supplied
    ...
```

**Files:** `phase2_validator.py` (a few lines).
**Risk:** Low — additive, backward-compatible.

### 3. `phase3_session.py` — validate a chunk's claims concurrently (no deferral)

**Current:** `ingest_chunk()` transcribes, detects, then validates claims **one at a
time** in a loop.

**Change:** make `ingest_chunk` `async`, keep the per-chunk order intact, and replace
the sequential validation loop with a concurrent `gather` over *this chunk's* claims.
Crucially, **preserve everything the current method does** — VAD silence-gating,
Whisper retry/backoff, status pings, and the warn-and-continue path on persistent
failure. No claim is deferred to a later chunk.

```python
async def ingest_chunk(self, wav_bytes):
    self.status = "processing"
    if self._is_silent(wav_bytes):          # keep VAD gate
        self.status = "listening"
        return []
    try:
        text = await asyncio.to_thread(transcribe_chunk, self.openai, wav_bytes)  # keep retry inside
    finally:
        self.status = "listening"
    if text:
        self.rolling_transcript = (self.rolling_transcript + " " + text).strip()
        self._flush_recovery()
    claims = self.detector.feed_text(text)            # claims carry merged metric tags (Change 1)
    if not claims:
        return []
    alerts = await asyncio.gather(*[
        asyncio.to_thread(self._validate_one, c) for c in claims
    ])
    return [a for a in alerts if a]
```

- `_validate_one(claim)` wraps the existing per-claim path, passing the pre-supplied
  `tag` into `validate_claim(..., tag=claim.tag)` (Change 2), appending to
  `self.alerts`, and bumping `claim_count` (guard shared-state mutation if needed).
- **`finalize()`** must validate the flushed trailing claims the same concurrent way,
  so the meeting's last claims aren't dropped.
- **Server call-site:** `phase3_server_realtime.py` currently does
  `run_in_threadpool(session.ingest_chunk, body)`; change it to
  `await session.ingest_chunk(body)` (and likewise for `finalize`).
- **Ordering:** keep chunks strictly sequential per session — the server already
  awaits one chunk before the next; if chunks can overlap, add a per-session
  `asyncio.Lock` around `ingest_chunk`.

**Files:** `phase3_session.py` (async refactor of the validation loop, preserving
gating/retry/status/finalize) + a one-line `await` change in `phase3_server_realtime.py`.
**Risk:** Medium — async refactor of the core loop; test carefully. Lower than the
original cross-chunk version because it neither defers alerts nor drops functionality.

---

## Not doing now: cross-chunk STT/validate overlap

The first draft overlapped STT(N) with validation of chunk **N-1**. Skip it because:

- It **defers each alert by one chunk**. At 4–6s VAD chunks that's +4–6s — worse than
  today, not better. It only helps if chunks are ~2s.
- It drops VAD gating, retries, status, and the failure path.
- It needs extra plumbing (await at the call-site, finalize handling, ordering lock,
  `_pending_claims` init) that the draft omitted.

Revisit **only** if the system moves to small (~2s) fixed chunks. Until then, in-chunk
concurrency (Change 3) is the right parallelism.

---

## Test plan

1. **Unit:** `py -3.12 test_timeline.py` — temporal logic still 8/8.
2. **Unit:** `py -3.12 test_pending.py` — provisional/pending still 9/9.
3. **Multi-claim ordering (new):** feed one chunk that yields 3 claims → all 3 alerts
   appear, none dropped, order preserved despite concurrent validation.
4. **Integration:** start server, run streamer, speak:
   - "QA is at 82 percent" → VERIFIED
   - "The budget is one million dollars" → CONTRADICTED (SOW says $500K)
   - "The release is June 21st" → VERIFIED
5. **Latency check:** measure end-of-sentence → alert **at the real 4–6s chunk size**;
   target < 4s. (Don't benchmark at 2s — that's not how the streamer cuts.)
6. **Failure path:** drop the network for one chunk → "transcription hiccup" warning,
   session continues on the next chunk (must still hold after the async refactor).

---

## Rollback

Changes are isolated. If anything breaks:
- Revert `phase3_claims.py` → detection works; tag falls back to the separate call.
- Revert `phase2_validator.py` → 2-line undo, zero impact.
- Revert `phase3_session.py` (+ the server `await`) → back to the synchronous loop.

```bash
git checkout geppetto-3 -- phase3_claims.py phase2_validator.py phase3_session.py phase3_server_realtime.py
```

---

## Estimated effort

| Task | Time |
|---|---|
| Merge detect + tag prompt (`phase3_claims.py`) | 30 min |
| Accept pre-tag (`phase2_validator.py`) | 10 min |
| Concurrent validation + async refactor (`phase3_session.py` + server `await`) | 60 min |
| Testing + verification (incl. multi-claim ordering, failure path) | 45 min |
| **Total** | **~2.5 hours** |

---

## Decision gate

Approve only if:
- [ ] Option A is already applied and tested.
- [ ] All existing tests pass before starting (`test_timeline.py`, `test_pending.py`).
- [ ] ~3 hours available before any demo (async refactor needs real testing).
- [ ] You accept that the big lever is chunk-fill time (4–6s); these changes trim the
  processing tail, not the fill.
