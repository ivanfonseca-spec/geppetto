# Geppetto 3 — Temporal Truth Layer

Evolution of **Geppetto 2** (the stable real-time Meeting Truth Layer). Geppetto 3
adds a sense of **time**: the knowledge base becomes a versioned timeline of facts,
so progress that changes (QA 15% → 23% → 34% → 82%) is handled correctly — claims
are judged against the *current* truth, superseded values read as OUTDATED (with the
current value surfaced), and the KB can advance without losing history.

## Start here

- **`GEPPETTO_3_DESIGN.md`** — the design: prose vs. versioned facts, freshness-aware
  validation, the data model, update paths, and open decisions.
- **`GEPPETTO_3_PLAN.md`** — the build roadmap (setup → fact store → freshness-aware
  validator → update paths → verification).

## What's in this folder

A fork of the working Geppetto 2 real-time stack, ready to build on:

- Real-time pipeline: `phase1_audio_streaming.py`, `phase3_server_realtime.py`,
  `phase3_session.py`, `phase3_claims.py`, `phase3_websocket.py`
- Reused engine: `phase2_validator.py`, `phase2_kb_setup.py`, `phase3_integration.py`,
  `phase3_storage.py`, `chroma_data/`
- Test/demo tools: `test_feed_clip.py`, `test_claims_text.py`, `make_claims_wav.py`,
  `record_to_wav.py`
- Docs: `RUN_REALTIME.md` (how to run the inherited system), `HOW_IT_WORKS.md`

## How to run (inherited behavior)

See `RUN_REALTIME.md`. In short: `python phase3_server_realtime.py`, open
`http://127.0.0.1:8000`, and feed audio with `test_feed_clip.py`.

## Status

Forked from Geppetto 2 at the point where the real-time system is complete and
acceptance-tested. The temporal-truth work (the reason this project exists) has
**not started yet** — begin with Step 1 of `GEPPETTO_3_PLAN.md` (the fact store).

## Carryover decisions (still apply)

- STT = `whisper-1`, no priming (gpt-4o models corrupted numbers in testing).
- Treat transcribed **numbers as fragile** — doubly important here, since facts are
  mostly numeric/dated.
- Local-first, Windows, no GPU; OpenAI (Whisper) + Anthropic (Haiku) the only cloud calls.
