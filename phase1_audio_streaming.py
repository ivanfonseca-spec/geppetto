"""
PHASE 1 (real-time): CHUNKED AUDIO STREAMER
===========================================
Captures meeting audio from VB-Cable, splits it on natural pauses (VAD-aligned
~4-6s chunks with a little overlap), skips near-silent chunks, and streams each
chunk to the real-time server. Runs on the PM's Windows laptop.

Design choices come from the spike:
  - VAD-aligned cuts (not fixed time) so words aren't split at boundaries.
  - Silence gating so we don't pay for / hallucinate on dead air.
  - whisper-1 (server side) with NO priming.

The chunking logic lives in StreamChunker (pure, unit-tested). pyaudio and the
network calls are only imported inside the runtime functions, so the module
imports fine anywhere for testing.

Usage:
  py phase1_audio_streaming.py --server http://localhost:8000
  py phase1_audio_streaming.py --server http://localhost:8000 --session abc123
  (Ctrl+C to stop — it finalizes the session and prints the saved folder.)
"""

import sys
import json
import time
import wave
import argparse
import threading
import urllib.request
from array import array
from collections import deque
from queue import Queue, Empty

RATE = 16000
FRAME_MS = 30
MIN_CHUNK_SEC = 2.5
MAX_CHUNK_SEC = 4.0
OVERLAP_SEC = 0.5
SILENCE_RATIO = 0.3      # frame is silence if RMS < ratio * rolling median
MIN_ABS_RMS = 50.0       # absolute floor so quiet-but-present speech isn't gated


# ----------------------------------------------------------------------------
# VAD-aligned streaming chunker (no pyaudio / no network — testable)
# ----------------------------------------------------------------------------
class StreamChunker:
    def __init__(self, rate=RATE, frame_ms=FRAME_MS, min_sec=MIN_CHUNK_SEC,
                 max_sec=MAX_CHUNK_SEC, overlap_sec=OVERLAP_SEC,
                 silence_ratio=SILENCE_RATIO, min_abs_rms=MIN_ABS_RMS,
                 window_frames=100):
        self.rate = rate
        self.frame_len = int(rate * frame_ms / 1000)
        self.min_sec = min_sec
        self.max_sec = max_sec
        self.overlap_samples = int(overlap_sec * rate)
        self.silence_ratio = silence_ratio
        self.min_abs = min_abs_rms
        self.window = deque(maxlen=window_frames)
        self.cur = array("h")

    @staticmethod
    def _rms(samples):
        if not samples:
            return 0.0
        acc = 0
        for x in samples:
            acc += x * x
        return (acc / len(samples)) ** 0.5

    def _threshold(self):
        if len(self.window) < 10:
            return self.min_abs
        med = sorted(self.window)[len(self.window) // 2]
        return max(self.min_abs, self.silence_ratio * med)

    def _emit(self, chunk, thr):
        # seed next chunk with a short overlap from this one
        self.cur = array("h")
        if self.overlap_samples and len(chunk) > self.overlap_samples:
            self.cur.extend(chunk[-self.overlap_samples:])
        return {"samples": chunk, "silent": self._rms(chunk) < thr,
                "dur": len(chunk) / self.rate}

    def add_frame(self, frame_samples):
        """Feed one ~frame_ms frame (array('h')). Returns a chunk dict at a
        boundary, else None."""
        self.window.append(self._rms(frame_samples))
        self.cur.extend(frame_samples)
        dur = len(self.cur) / self.rate
        thr = self._threshold()
        is_sil = self.window[-1] < thr
        if (dur >= self.min_sec and is_sil) or dur >= self.max_sec:
            return self._emit(self.cur, thr)
        return None

    def flush(self):
        """Emit whatever remains (call when capture stops)."""
        if len(self.cur) < self.frame_len:
            return None
        chunk = self.cur
        self.cur = array("h")
        return {"samples": chunk, "silent": self._rms(chunk) < self._threshold(),
                "dur": len(chunk) / self.rate}


def chunk_to_wav_bytes(samples, rate=RATE):
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


# ----------------------------------------------------------------------------
# HTTP (stdlib only)
# ----------------------------------------------------------------------------
def _post(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def start_session(server):
    status, body = _post(f"{server}/api/session/start")
    sid = json.loads(body).get("session_id")
    print(f"Session started: {sid}")
    print(f"Open the dashboard at: {server}/?session={sid}")
    return sid


def send_chunk(server, sid, wav_bytes):
    return _post(f"{server}/api/session/{sid}/chunk", data=wav_bytes,
                 headers={"Content-Type": "audio/wav"})


def end_session(server, sid):
    status, body = _post(f"{server}/api/session/{sid}/end")
    try:
        return json.loads(body).get("folder_name")
    except Exception:
        return None


# ----------------------------------------------------------------------------
# VB-Cable device
# ----------------------------------------------------------------------------
def find_vb_cable(pyaudio_mod):
    p = pyaudio_mod.PyAudio()
    idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and "CABLE" in info["name"].upper():
            idx = i
            break
    p.terminate()
    if idx is None:
        sys.exit("VB-Cable not found. Install it and set CABLE Input as default playback.")
    return idx


# ----------------------------------------------------------------------------
# main capture loop
# ----------------------------------------------------------------------------
def run(server, session_id=None, max_seconds=None):
    import pyaudio

    sid = session_id or start_session(server)
    device = None  # use system default input (Jabra mic)

    # background sender so capture isn't blocked by the network
    q = Queue()
    stop = threading.Event()

    def sender():
        while not (stop.is_set() and q.empty()):
            try:
                wav = q.get(timeout=0.2)
            except Empty:
                continue
            for attempt in range(3):
                try:
                    send_chunk(server, sid, wav)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  [warn] chunk dropped after retries: {str(e)[:60]}")
                    else:
                        time.sleep(0.5 * (2 ** attempt))

    t = threading.Thread(target=sender, daemon=True)
    t.start()

    chunker = StreamChunker()
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                    input_device_index=device, frames_per_buffer=chunker.frame_len)
    print("Listening… (Ctrl+C to stop)")

    sent = 0
    started = time.time()
    try:
        while True:
            raw = stream.read(chunker.frame_len, exception_on_overflow=False)
            frame = array("h"); frame.frombytes(raw)
            chunk = chunker.add_frame(frame)
            if chunk and not chunk["silent"]:
                q.put(chunk_to_wav_bytes(chunk["samples"]))
                sent += 1
                print(f"  chunk {sent} sent ({chunk['dur']:.1f}s)")
            if max_seconds and (time.time() - started) >= max_seconds:
                break
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        tail = chunker.flush()
        if tail and tail["dur"] > 0.5:  # send anything > 0.5s on exit
            q.put(chunk_to_wav_bytes(tail["samples"]))
        stream.stop_stream(); stream.close(); p.terminate()
        stop.set(); t.join(timeout=15)
        folder = end_session(server, sid)
        print(f"Session ended. Saved: {folder}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--session", default=None, help="attach to an existing session id")
    ap.add_argument("--max-seconds", type=int, default=None)
    args = ap.parse_args()
    run(args.server, session_id=args.session, max_seconds=args.max_seconds)


if __name__ == "__main__":
    main()
