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
        print("[warn] VB-Cable not found — falling back to system default microphone. "
              "(Install VB-Cable and set CABLE Input as default playback for full loopback.)")
        return None  # None = system default input device
    return idx


def list_devices(pyaudio_mod):
    p = pyaudio_mod.PyAudio()
    print("\nAvailable input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"  [{i}] {info['name']}")
    p.terminate()


# ----------------------------------------------------------------------------
# audio sources (single device, or mic + system-audio mixed)
# ----------------------------------------------------------------------------
def _import_pyaudio():
    """Prefer PyAudioWPatch (adds WASAPI loopback for system-audio capture);
    fall back to plain pyaudio. Returns (module, has_wasapi_loopback)."""
    try:
        import pyaudiowpatch as pyaudio
        return pyaudio, True
    except ImportError:
        import pyaudio
        return pyaudio, False


def _find_cable_index(pyaudio_mod):
    """Return the VB-Cable input device index, or None if not present."""
    p = pyaudio_mod.PyAudio()
    idx = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and "CABLE" in info["name"].upper():
            idx = i
            break
    p.terminate()
    return idx


class _SingleSource:
    """Capture from one input device at 16 kHz mono."""
    def __init__(self, pyaudio_mod, device_index, frame_len):
        self.frame_len = frame_len
        self.p = pyaudio_mod.PyAudio()
        self.stream = self.p.open(
            format=pyaudio_mod.paInt16, channels=1, rate=RATE, input=True,
            input_device_index=device_index, frames_per_buffer=frame_len)

    def read(self):
        raw = self.stream.read(self.frame_len, exception_on_overflow=False)
        f = array("h"); f.frombytes(raw)
        return f

    def close(self):
        try:
            self.stream.stop_stream(); self.stream.close()
        finally:
            self.p.terminate()


class _MixSource:
    """Capture the microphone AND the system output (headset) and mix them.

    Mic is opened at 16 kHz mono. System audio uses WASAPI loopback (captures
    whatever plays in the headset — remote participants) when PyAudioWPatch is
    available, else VB-Cable. Each source is read in its own thread into a
    buffer; read() pulls one frame from each and sums them (clipped to int16).
    """
    def __init__(self, pyaudio_mod, frame_len):
        self.pa = pyaudio_mod
        self.frame_len = frame_len
        self.p = pyaudio_mod.PyAudio()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.buf_mic = array("h")
        self.buf_sys = array("h")

        # microphone (your voice) — 16 kHz mono
        self.mic_stream = self.p.open(
            format=pyaudio_mod.paInt16, channels=1, rate=RATE, input=True,
            input_device_index=None, frames_per_buffer=frame_len)
        print("  mic: system default microphone (16 kHz mono)")

        # system audio (what plays in the headset) — loopback
        self.sys_stream, self.sys_rate, self.sys_ch = self._open_loopback()

        threading.Thread(target=self._mic_reader, daemon=True).start()
        if self.sys_stream:
            threading.Thread(target=self._sys_reader, daemon=True).start()

    def _open_loopback(self):
        pa, p = self.pa, self.p
        # 1) WASAPI loopback (PyAudioWPatch) — no VB-Cable / no routing needed
        if hasattr(p, "get_default_wasapi_loopback"):
            try:
                info = p.get_default_wasapi_loopback()
                rate = int(info["defaultSampleRate"])
                ch = int(info["maxInputChannels"]) or 2
                st = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=info["index"],
                            frames_per_buffer=int(rate * FRAME_MS / 1000))
                print(f"  system: WASAPI loopback '{info['name']}' ({rate} Hz x{ch})")
                return st, rate, ch
            except Exception as e:
                print(f"  [warn] WASAPI loopback failed: {str(e)[:70]}")
        # 2) VB-Cable @ 16 kHz mono
        idx = _find_cable_index(pa)
        if idx is not None:
            st = p.open(format=pa.paInt16, channels=1, rate=RATE, input=True,
                        input_device_index=idx, frames_per_buffer=self.frame_len)
            print("  system: VB-Cable loopback (16 kHz mono)")
            return st, RATE, 1
        print("  [warn] No system-audio source found — capturing MIC ONLY.\n"
              "         For headset/remote audio: py -3.12 -m pip install PyAudioWPatch")
        return None, RATE, 1

    def _norm(self, raw):
        """Downmix to mono and resample the loopback stream to 16 kHz."""
        a = array("h"); a.frombytes(raw)
        mono = a[0::self.sys_ch] if self.sys_ch >= 2 else a   # left channel
        if self.sys_rate != RATE and len(mono):
            step = self.sys_rate / RATE
            mono = array("h", [mono[int(i * step)]
                               for i in range(int(len(mono) / step))])
        return mono

    def _mic_reader(self):
        while not self.stop.is_set():
            try:
                raw = self.mic_stream.read(self.frame_len, exception_on_overflow=False)
            except Exception:
                break
            f = array("h"); f.frombytes(raw)
            with self.lock:
                self.buf_mic.extend(f)

    def _sys_reader(self):
        n = int(self.sys_rate * FRAME_MS / 1000)
        while not self.stop.is_set():
            try:
                raw = self.sys_stream.read(n, exception_on_overflow=False)
            except Exception:
                break
            mono = self._norm(raw)
            with self.lock:
                self.buf_sys.extend(mono)
                # keep the system buffer from drifting far ahead of the mic
                cap = self.frame_len * 50
                if len(self.buf_sys) > cap:
                    del self.buf_sys[:len(self.buf_sys) - cap]

    def read(self):
        n = self.frame_len
        while not self.stop.is_set():
            with self.lock:
                if len(self.buf_mic) >= n:
                    mic = self.buf_mic[:n]; del self.buf_mic[:n]
                    if len(self.buf_sys) >= n:
                        sysf = self.buf_sys[:n]; del self.buf_sys[:n]
                    else:
                        sysf = array("h", [0]) * n    # system silent / behind
                    out = array("h", [0]) * n
                    for i in range(n):
                        v = mic[i] + sysf[i]
                        out[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
                    return out
            time.sleep(0.005)
        return None

    def close(self):
        self.stop.set()
        for st in (self.mic_stream, self.sys_stream):
            if st:
                try:
                    st.stop_stream(); st.close()
                except Exception:
                    pass
        try:
            self.p.terminate()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# main capture loop
# ----------------------------------------------------------------------------
def _capture_loop(server, sid, source, max_seconds=None):
    """Pull frames from a source, VAD-chunk them, and stream chunks to the server."""
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
    print("Listening… (Ctrl+C to stop)")
    sent = 0
    started = time.time()
    try:
        while True:
            frame = source.read()
            if frame is None:
                break
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
        source.close()
        stop.set(); t.join(timeout=15)
        folder = end_session(server, sid)
        print(f"Session ended. Saved: {folder}")


def run(server, session_id=None, max_seconds=None, device_mode="vbcable"):
    pyaudio, _has_wpatch = _import_pyaudio()

    if device_mode == "list":
        list_devices(pyaudio)
        return

    sid = session_id or start_session(server)
    frame_len = int(RATE * FRAME_MS / 1000)

    if device_mode == "both":
        print("Capture mode: mic + system audio (mixed)")
        source = _MixSource(pyaudio, frame_len)
    elif device_mode == "mic":
        print("Capture mode: system default microphone")
        source = _SingleSource(pyaudio, None, frame_len)
    else:
        device = find_vb_cable(pyaudio)  # loopback — all call participants
        print(f"Capture mode: VB-Cable (device index {device})")
        source = _SingleSource(pyaudio, device, frame_len)

    _capture_loop(server, sid, source, max_seconds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--session", default=None, help="attach to an existing session id")
    ap.add_argument("--max-seconds", type=int, default=None)
    ap.add_argument(
        "--device", default="vbcable",
        choices=["vbcable", "mic", "both", "list"],
        help=(
            "vbcable (default): capture all call audio via VB-Cable loopback; "
            "mic: use system default microphone only; "
            "both: capture mic + system/headset audio and mix them; "
            "list: print available input devices and exit"
        )
    )
    args = ap.parse_args()
    run(args.server, session_id=args.session, max_seconds=args.max_seconds,
        device_mode=args.device)


if __name__ == "__main__":
    main()
