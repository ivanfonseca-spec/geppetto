"""
SPIKE: STT model comparison  (whisper-1 vs gpt-4o-transcribe family)
====================================================================
Same VAD-aligned chunks, transcribed by each candidate model, scored against
the best available whole-file reference (gpt-4o-transcribe). No priming — the
earlier spike showed rolling-transcript priming causes whisper-1 to hallucinate.

Goal: decide which STT model the live pipeline should use (task #3).
Lower WER = closer to the best full-context transcription. Also read the
transcripts in the JSON to judge whether project-style CLAIMS (numbers, dates,
statuses, names) survive — that matters more than raw WER.

Run on YOUR machine.  .wav needs no extra libs.
Usage:  py phase1_spike_compare.py "meetings\\clip_2min.wav"
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
MODELS = ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"]
REFERENCE_MODEL = "gpt-4o-transcribe"   # whole-file = best available "truth"
MIN_CHUNK_SEC = 3.0
MAX_CHUNK_SEC = 8.0
OVERLAP_SEC = 0.5
SILENCE_RATIO = 0.5
FRAME_MS = 30
LANGUAGE = "en"

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ----------------------------------------------------------------------------
# AUDIO (wav native; mp3/m4a via pydub+ffmpeg)
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
        sys.exit("Non-wav needs pydub+ffmpeg, or: ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav")
    seg = AudioSegment.from_file(path)
    return {"backend": "pydub", "seg": seg, "rate": seg.frame_rate,
            "width": seg.sample_width, "channels": seg.channels}


def get_mono_int16(audio):
    if audio["backend"] == "wave":
        if audio["width"] != 2:
            sys.exit("Expected 16-bit WAV: ffmpeg -i in -ar 16000 -ac 1 out.wav")
        s = array.array("h"); s.frombytes(audio["frames"])
        if audio["channels"] > 1:
            s = s[0::audio["channels"]]
        return s, audio["rate"]
    seg = audio["seg"].set_channels(1).set_frame_rate(16000).set_sample_width(2)
    s = array.array("h"); s.frombytes(seg.raw_data)
    return s, 16000


def duration_sec(audio):
    if audio["backend"] == "wave":
        return (len(audio["frames"]) / (audio["width"] * audio["channels"])) / audio["rate"]
    return len(audio["seg"]) / 1000.0


def write_slice(audio, start_s, end_s):
    fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    if audio["backend"] == "wave":
        rate, width, ch = audio["rate"], audio["width"], audio["channels"]
        bpf = width * ch
        with contextlib.closing(wave.open(tmp, "wb")) as w:
            w.setnchannels(ch); w.setsampwidth(width); w.setframerate(rate)
            w.writeframes(audio["frames"][int(start_s*rate)*bpf:int(end_s*rate)*bpf])
    else:
        audio["seg"][int(start_s*1000):int(end_s*1000)].export(tmp, format="wav")
    return tmp


def vad_segments(samples, rate):
    flen = max(1, int(rate * FRAME_MS / 1000))
    n = len(samples) // flen
    if n == 0:
        return [(0.0, len(samples) / rate)]
    rms = []
    for i in range(n):
        fr = samples[i*flen:(i+1)*flen]; acc = 0
        for x in fr:
            acc += x*x
        rms.append((acc/len(fr))**0.5)
    thr = max(1.0, SILENCE_RATIO * sorted(rms)[len(rms)//2])
    fdur = FRAME_MS/1000.0
    segs = []; start = 0.0
    for i in range(n):
        cur = (i+1)*fdur - start
        if (cur >= MIN_CHUNK_SEC and rms[i] < thr) or cur >= MAX_CHUNK_SEC:
            end = (i+1)*fdur; segs.append((start, end)); start = end
    total = n*fdur
    if start < total - 0.05:
        segs.append((start, total))
    return segs


# ----------------------------------------------------------------------------
# TRANSCRIPTION + SCORING
# ----------------------------------------------------------------------------
def transcribe(path, model):
    with open(path, "rb") as f:
        return client.audio.transcriptions.create(
            model=model, file=f, language=LANGUAGE).text.strip()


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
    d = [[0]*(len(h)+1) for _ in range(len(r)+1)]
    for i in range(len(r)+1): d[i][0] = i
    for j in range(len(h)+1): d[0][j] = j
    for i in range(1, len(r)+1):
        for j in range(1, len(h)+1):
            c = 0 if r[i-1] == h[j-1] else 1
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+c)
    return d[len(r)][len(h)] / len(r)


def chunked(audio, segments, total, model):
    transcript = ""; lat = []
    for (s, e) in segments:
        tmp = write_slice(audio, s, min(e + OVERLAP_SEC, total))
        try:
            t0 = time.time()
            text = transcribe(tmp, model)
            lat.append(time.time() - t0)
        finally:
            os.remove(tmp)
        transcript = merge_dedup(transcript, text)
    return transcript, lat


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: py phase1_spike_compare.py "path\\to\\clip.wav"')
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing from .env")

    audio = load_audio(path)
    total = duration_sec(audio)
    print("=" * 70)
    print("STT MODEL COMPARISON")
    print("=" * 70)
    print(f"File: {path} | {total:.1f}s")
    if total > 780:
        sys.exit("Clip too long (>13 min). Trim: ffmpeg -i in -ss 60 -t 120 -ar 16000 -ac 1 clip.wav")

    samples, rate = get_mono_int16(audio)
    segments = vad_segments(samples, rate)
    print(f"VAD chunks: {len(segments)}\n")

    # Best whole-file reference
    print(f"Reference (whole-file, {REFERENCE_MODEL}) ...")
    try:
        reference = transcribe(path, REFERENCE_MODEL)
    except Exception as e:
        print(f"  {REFERENCE_MODEL} failed ({str(e)[:80]}); falling back to whisper-1 reference")
        reference = transcribe(path, "whisper-1")
    print(f"  {len(reference.split())} words\n")

    results = {}
    for model in MODELS:
        print(f"Chunked: {model} ...")
        try:
            text, lat = chunked(audio, segments, total, model)
        except Exception as e:
            print(f"  SKIPPED — {str(e)[:100]}\n")
            results[model] = {"error": str(e)}
            continue
        s = sorted(lat)
        results[model] = {
            "wer": round(wer(reference, text), 4),
            "lat_avg": round(sum(s)/len(s), 2),
            "lat_p95": round(s[min(len(s)-1, int(0.95*len(s)))], 2),
            "lat_max": round(s[-1], 2),
            "transcript": text,
        }
        print(f"  WER {results[model]['wer']:.1%} | "
              f"lat avg/p95/max {results[model]['lat_avg']}/"
              f"{results[model]['lat_p95']}/{results[model]['lat_max']}s\n")

    print("=" * 70)
    print(f"{'MODEL':<26}{'WER':>8}{'avg':>8}{'p95':>8}{'max':>8}")
    print("-" * 70)
    for m in MODELS:
        r = results.get(m, {})
        if "error" in r:
            print(f"{m:<26}{'ERR':>8}")
        else:
            print(f"{m:<26}{r['wer']*100:>7.1f}%{r['lat_avg']:>7.2f}s"
                  f"{r['lat_p95']:>7.2f}s{r['lat_max']:>7.2f}s")
    print("=" * 70)
    print("Lower WER = closer to best whole-file transcription.")
    print("Now READ the transcripts in the JSON: do the numbers/dates/statuses survive?")

    out = {"file": path, "duration_sec": round(total, 1), "n_chunks": len(segments),
           "reference_model": REFERENCE_MODEL, "reference": reference, "models": results}
    with open("spike_compare_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("Full transcripts -> spike_compare_results.json")


if __name__ == "__main__":
    main()
