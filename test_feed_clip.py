"""
TEST FEEDER — push a WAV file through the live server (no VB-Cable needed)
=========================================================================
Smoke-tests the whole real-time path: chunk -> server -> whisper-1 -> claim
detection -> validation -> WebSocket -> dashboard. Use it to confirm the system
is alive before wiring up VB-Cable.

The WAV must be 16kHz mono 16-bit (the clip you made with
  ffmpeg -i in.mp3 -ss 60 -t 120 -ar 16000 -ac 1 clip_2min.wav
already is).

Usage:
  1) Start the server:   python phase3_server_realtime.py
  2) Run this:           python test_feed_clip.py "meetings\\clip_2min.wav"
     It starts a session and prints a dashboard URL — open it, then press Enter.
     (Or: click Start on the dashboard, then run with --session <id>.)
"""

import sys
import time
import wave
import array
import argparse

from phase1_audio_streaming import (
    StreamChunker, chunk_to_wav_bytes, start_session, send_chunk, end_session,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--session", default=None)
    ap.add_argument("--no-end", action="store_true", help="don't finalize/save at the end")
    args = ap.parse_args()

    w = wave.open(args.wav, "rb")
    rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    frames = w.readframes(w.getnframes()); w.close()
    if width != 2:
        sys.exit("WAV must be 16-bit: ffmpeg -i in -ar 16000 -ac 1 clip.wav")
    samples = array.array("h"); samples.frombytes(frames)
    if ch > 1:
        samples = samples[0::ch]

    sid = args.session
    if not sid:
        sid = start_session(args.server)
        url = f"{args.server}/?session={sid}"
        try:
            import webbrowser
            webbrowser.open(url)
            print(f"\nOpened dashboard in your browser: {url}")
        except Exception:
            print(f"\nOpen this in your browser: {url}")
        input("Wait for the dashboard to finish loading, then press Enter to start feeding… ")

    chunker = StreamChunker(rate=rate)
    flen = chunker.frame_len
    sent = 0
    i = 0
    while i + flen <= len(samples):
        chunk = chunker.add_frame(samples[i:i + flen])
        i += flen
        if chunk and not chunk["silent"]:
            send_chunk(args.server, sid, chunk_to_wav_bytes(chunk["samples"], rate))
            sent += 1
            print(f"  sent chunk {sent} ({chunk['dur']:.1f}s)")
            time.sleep(0.3)            # pace it so alerts stream visibly
    tail = chunker.flush()
    if tail and not tail["silent"]:
        send_chunk(args.server, sid, chunk_to_wav_bytes(tail["samples"], rate)); sent += 1

    print(f"\nFed {sent} chunks.")
    if not args.no_end:
        folder = end_session(args.server, sid)
        print(f"Session ended and saved: {folder}")
    else:
        print(f"Session left open: {sid}")


if __name__ == "__main__":
    main()
