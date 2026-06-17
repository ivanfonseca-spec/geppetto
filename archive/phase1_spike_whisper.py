"""
SPIKE: Chunked whisper-1 accuracy — VAD-ALIGNED version
=======================================================
Purpose (REQUIREMENTS_REALTIME.md §10.3): measure how much accuracy we lose by
transcribing short chunks instead of the whole file, using the SAME chunking
strategy the real pipeline will use — cutting on natural pauses (VAD), not at
fixed time marks. Fixed cuts slice through words and overstate the loss; this
version is the apples-to-apples test for Option A.

What it does
------------
1. Whole-file whisper-1 call  -> REFERENCE transcript.
2. Splits audio on SILENCE into ~MIN..MAX second chunks (energy-based VAD,
   no extra dependencies).
3. Transcribes the chunks two ways and reassembles (overlap de-duped):
     - "cold"   : each chunk independent
     - "primed" : each chunk primed with the running transcript tail
4. Reports WER of cold/primed vs reference, plus avg / p95 / max chunk latency
   and how many chunks blew past the latency target.

Run on YOUR machine (needs api.openai.com).  .wav needs no extra libs;
mp3/m4a needs pydub+ffmpeg (or just: ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav).

Usage:  py phase1_spike_whisper.py path\\to\\clip.wav
"""

import os
import sys
import time
import json
import wave
import array
import contextlib
import tempfile

from dotenv import load_dotenv
from openai import OpenAI

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
MIN_CHUNK_SEC = 3.0     # don't cut a chunk shorter than this (even at a pause)
MAX_CHUNK_SEC = 8.0     # force a cut by here if no pause was found
OVERLAP_SEC = 0.5       # small overlap carried into each chunk (boundary safety)
SILENCE_RATIO = 0.5     # a frame is "silence" if its RMS < ratio * median RMS
FRAME_MS = 30           # VAD analysis frame size
PROMPT_TAIL_CHARS = 200 # how much prior transcript primes each chunk
LATENCY_TARGET = 12.0   # NFR-1 budget, for the outlier count
MODEL = "whisper-1"
LANGUAGE = "en"
WHISPER_COST_PER_MIN = 0.006

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ----------------------------------------------------------------------------
# AUDIO LOADING
# ----------------------------------------------------------------------------
def load_audio(path):
    if path.lower().endswith(".wav"):
        with contextlib.closing(wave.open(path, "rb")) as w:
            p = w.getparams()
            frames = w.readframes(w.getnframes())
        return {"backend": "wave", "frames": frames, "rate": p.framerate,
                "width": p.sampwidth, "channels": p.nchannels}
    try:
        from pydub import AudioSegment
    except ImportError:
        sys.exit("Non-wav input needs pydub+ffmpeg, or convert first:\n"
                 '  ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav')
    seg = AudioSegment.from_file(path)
    return {"backend": "pydub", "seg": seg, "rate": seg.frame_rate,
            "width": seg.sample_width, "channels": seg.channels}


def get_mono_int16(audio):
    """Return (array('h') of mono 16-bit samples, sample_rate)."""
    if audio["backend"] == "wave":
        if audio["width"] != 2:
            sys.exit("Expected 16-bit WAV. Re-encode: ffmpeg -i in -ar 16000 -ac 1 out.wav")
        samples = array.array("h")
        samples.frombytes(audio["frames"])
        if audio["channels"] > 1:
            samples = samples[0::audio["channels"]]  # take channel 0
        return samples, audio["rate"]
    seg = audio["seg"].set_channels(1).set_frame_rate(16000).set_sample_width(2)
    samples = array.array("h")
    samples.frombytes(seg.raw_data)
    return samples, 16000


def duration_sec(audio):
    if audio["backend"] == "wave":
        n = len(audio["frames"]) / (audio["width"] * audio["channels"])
        return n / audio["rate"]
    return len(audio["seg"]) / 1000.0


def write_slice(audio, start_s, end_s):
    fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    if audio["backend"] == "wave":
        rate, width, ch = audio["rate"], audio["width"], audio["channels"]
        bpf = width * ch
        s = int(start_s * rate) * bpf
        e = int(end_s * rate) * bpf
        with contextlib.closing(wave.open(tmp, "wb")) as w:
            w.setnchannels(ch); w.setsampwidth(width); w.setframerate(rate)
            w.writeframes(audio["frames"][s:e])
    else:
        audio["seg"][int(start_s * 1000):int(end_s * 1000)].export(tmp, format="wav")
    return tmp


# ----------------------------------------------------------------------------
# VAD-ALIGNED SEGMENTATION (energy-based, no extra deps)
# Cut on a low-energy frame once the chunk is >= MIN, force a cut at MAX.
# ----------------------------------------------------------------------------
def vad_segments(samples, rate):
    flen = max(1, int(rate * FRAME_MS / 1000))
    nframes = len(samples) // flen
    if nframes == 0:
        return [(0.0, len(samples) / rate)]

    rms = []
    for i in range(nframes):
        fr = samples[i * flen:(i + 1) * flen]
        acc = 0
        for x in fr:
            acc += x * x
        rms.append((acc / len(fr)) ** 0.5)

    median = sorted(rms)[len(rms) // 2]
    thr = max(1.0, SILENCE_RATIO * median)
    fdur = FRAME_MS / 1000.0

    segs = []
    start = 0.0
    for i in range(nframes):
        cur = (i + 1) * fdur - start
        is_sil = rms[i] < thr
        if (cur >= MIN_CHUNK_SEC and is_sil) or cur >= MAX_CHUNK_SEC:
            end = (i + 1) * fdur
            segs.append((start, end))
            start = end
    total = nframes * fdur
    if start < total - 0.05:
        segs.append((start, total))
    return segs


# ----------------------------------------------------------------------------
# TRANSCRIPTION + REASSEMBLY
# ----------------------------------------------------------------------------
def transcribe(path, prompt=None):
    with open(path, "rb") as f:
        kw = dict(model=MODEL, file=f, language=LANGUAGE)
        if prompt:
            kw["prompt"] = prompt
        return client.audio.transcriptions.create(**kw).text.strip()


def merge_dedup(prev, new):
    if not prev:
        return new
    pw, nw = prev.split(), new.split()
    for k in range(min(len(pw), len(nw), 12), 0, -1):
        if [w.lower().strip(".,!?") for w in pw[-k:]] == \
           [w.lower().strip(".,!?") for w in nw[:k]]:
            return " ".join(pw + nw[k:])
    return " ".join(pw + nw)


def normalize(t):
    return [w.lower().strip(".,!?;:\"'") for w in t.split() if w.strip()]


def wer(ref, hyp):
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            c = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
    return d[len(r)][len(h)] / len(r)


def chunked_transcribe(audio, segments, total, primed):
    transcript = ""
    latencies = []
    for (start, end) in segments:
        tmp = write_slice(audio, start, min(end + OVERLAP_SEC, total))
        try:
            prompt = transcript[-PROMPT_TAIL_CHARS:] if primed and transcript else None
            t0 = time.time()
            text = transcribe(tmp, prompt=prompt)
            latencies.append(time.time() - t0)
        finally:
            os.remove(tmp)
        transcript = merge_dedup(transcript, text)
    return transcript, latencies


def stats(latencies):
    s = sorted(latencies)
    avg = sum(s) / len(s)
    p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
    return avg, p95, s[-1]


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: py phase1_spike_whisper.py path\\to\\audio.wav")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing from .env")

    print("=" * 70)
    print("WHISPER CHUNKING SPIKE  (VAD-aligned)")
    print("=" * 70)
    audio = load_audio(path)
    total = duration_sec(audio)
    print(f"File: {path}\nDuration: {total:.1f}s | {audio['rate']}Hz | {audio['channels']}ch")

    if total > 780:
        sys.exit(f"Clip is {total/60:.1f} min — too long (whole-file pass hits "
                 f"Whisper's 25MB limit). Trim to 1-2 min:\n"
                 f'  ffmpeg -i in -ss 60 -t 120 -ar 16000 -ac 1 clip.wav')

    samples, rate = get_mono_int16(audio)
    segments = vad_segments(samples, rate)
    seg_lens = [e - s for s, e in segments]
    print(f"VAD chunks: {len(segments)} "
          f"(avg {sum(seg_lens)/len(seg_lens):.1f}s, "
          f"min {min(seg_lens):.1f}s, max {max(seg_lens):.1f}s)\n")

    print("[1/3] Reference (whole-file) ...")
    t0 = time.time()
    reference = transcribe(path)
    ref_words = len(reference.split())
    print(f"      {ref_words} words in {time.time()-t0:.1f}s")
    if total > 0 and ref_words / total < 0.5:
        print("      ⚠ Very few words for this duration — is this speech? "
              "(music/silence makes WER meaningless)")
    print()

    print("[2/3] Chunked COLD ...")
    cold, cold_lat = chunked_transcribe(audio, segments, total, primed=False)
    print("[3/3] Chunked PRIMED ...")
    primed, primed_lat = chunked_transcribe(audio, segments, total, primed=True)

    wer_cold, wer_primed = wer(reference, cold), wer(reference, primed)
    all_lat = cold_lat + primed_lat
    avg, p95, mx = stats(all_lat)
    over = sum(1 for x in all_lat if x > LATENCY_TARGET)
    cost = (total / 60.0) * WHISPER_COST_PER_MIN * 3

    print("\n" + "=" * 70)
    print("RESULTS  (lower WER = closer to whole-file reference)")
    print("=" * 70)
    print(f"  VAD chunks:            {len(segments)}")
    print(f"  WER cold:              {wer_cold:.1%}")
    print(f"  WER primed:            {wer_primed:.1%}")
    print(f"  Priming improvement:   {(wer_cold - wer_primed):+.1%}")
    print(f"  Latency avg/p95/max:   {avg:.2f}s / {p95:.2f}s / {mx:.2f}s")
    print(f"  Chunks over {LATENCY_TARGET:.0f}s:       {over} of {len(all_lat)}")
    print(f"  Spike API cost (3x):   ~${cost:.3f}")
    if wer_primed <= 0.10:
        verdict = "PASS — VAD chunking holds accuracy; proceed with Option A."
    elif wer_primed <= 0.20:
        verdict = "MARGINAL — usable but watch boundary loss; consider streaming STT later."
    else:
        verdict = ("REVIEW — high loss even with VAD cuts; lean toward streaming "
                   "STT (gpt-4o-transcribe) rather than chunked whisper-1.")
    print(f"  Verdict: {verdict}")
    print("=" * 70)

    out = {"file": path, "duration_sec": round(total, 1), "n_chunks": len(segments),
           "min_chunk_sec": MIN_CHUNK_SEC, "max_chunk_sec": MAX_CHUNK_SEC,
           "overlap_sec": OVERLAP_SEC, "ref_words": ref_words,
           "wer_cold": round(wer_cold, 4), "wer_primed": round(wer_primed, 4),
           "latency_avg": round(avg, 3), "latency_p95": round(p95, 3),
           "latency_max": round(mx, 3), "chunks_over_target": over,
           "reference": reference, "cold": cold, "primed": primed}
    with open("spike_whisper_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("Full transcripts + metrics -> spike_whisper_results.json")


if __name__ == "__main__":
    main()
