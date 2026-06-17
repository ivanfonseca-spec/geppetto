# Whisper Chunking Spike — Runbook

**Goal:** measure how much transcription accuracy we lose by sending short overlapping
chunks to `whisper-1` (what the live pipeline will do) versus transcribing the whole
file at once — and whether priming each chunk with the prior transcript tail recovers it.
This validates the riskiest assumption in the real-time build (REQUIREMENTS_REALTIME.md §10.3)
**before** we build the rest of the pipeline.

> Run this on your Windows machine. It needs network access to `api.openai.com`,
> which my build sandbox can't reach — so I can't run it for you.

## 1. Setup

```bash
pip install openai python-dotenv
# .wav inputs need nothing else. For mp3/m4a:
pip install pydub        # and install ffmpeg (https://ffmpeg.org/download.html)
```

`.env` already has `OPENAI_API_KEY`, so no extra config.

## 2. Get a test clip

Any 1–2 minute meeting recording works. Options:

- Use an existing `.wav`/`.mp3` of a real meeting (best — real accents, crosstalk, jargon).
- Or capture one: run `python phase1_audio_pipeline.py` while audio plays through VB-Cable; it saves a `.wav`.

A real meeting clip gives the truest read; clean/single-speaker audio will understate boundary problems.

## 3. Run

```bash
python phase1_spike_whisper.py path\to\meeting.wav
```

## 4. Read the output

The script prints a results block and writes `spike_whisper_results.json` (full transcripts + metrics). Key numbers:

| Metric | Meaning |
|---|---|
| **WER cold** | Word error rate of chunked-without-priming vs whole-file reference |
| **WER primed** | Same, but each chunk primed with the prior transcript tail |
| **Priming improvement** | How much context priming helped (want this positive) |
| **Max chunk latency** | Slowest single-chunk Whisper round-trip (feeds the ≤12s NFR-1 budget) |

**Interpretation:**

- **WER primed ≤ ~10%** → chunked transcription is good enough; proceed with Option A as specced.
- **WER primed materially > 10%**, or priming barely helps → boundary loss is real. Try widening `CHUNK_SEC` to 6–8s and/or `OVERLAP_SEC` to ~1s (top of `phase1_spike_whisper.py`) and re-run. If it's still poor, that's the signal to move to streaming STT (`gpt-4o-transcribe`, §12) sooner rather than later.

## 5. What to send back

Paste me the printed results block (or the JSON). I'll use the WER + latency numbers to lock chunk size / overlap defaults before building the audio streamer (task #2) and the server transcription path (task #3).

## Tuning knobs (top of the script)

- `CHUNK_SEC` (5.0) — nominal chunk length
- `OVERLAP_SEC` (0.75) — overlap to protect words split at boundaries
- `PROMPT_TAIL_CHARS` (200) — how much prior transcript is fed as context
- `WHISPER_COST_PER_MIN` (0.006) — set to your actual rate for the cost estimate
