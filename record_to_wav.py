"""
RECORD TO WAV
=============
Capture audio from VB-Cable (loopback) and save it to a .wav file you name.
Use it to grab a real-speech test clip for phase1_spike_whisper.py.

Usage:
  python record_to_wav.py meetings\\clip1.wav 90      # record 90 seconds
  python record_to_wav.py meetings\\clip1.wav         # default 60 seconds

Setup (one time):
  1. Windows Sound settings -> set "CABLE Input (VB-Audio Virtual Cable)" as the
     DEFAULT PLAYBACK device. (Now all PC audio flows into VB-Cable.)
  2. To still HEAR it: Sound Control Panel -> Recording -> "CABLE Output" ->
     Properties -> Listen -> tick "Listen to this device" -> pick your headphones.
  3. Play the YouTube video, then run this script for the length you want.
"""

import os
import sys
import wave
import pyaudio

SAMPLE_RATE = 16000   # 16kHz mono = what Whisper expects
CHANNELS = 1
FORMAT = pyaudio.paInt16
FRAMES_PER_BUFFER = 1024


def find_vb_cable():
    p = pyaudio.PyAudio()
    idx = None
    print("Audio input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"  [{i}] {info['name']}")
            if "CABLE" in info["name"].upper() and idx is None:
                idx = i
    p.terminate()
    if idx is None:
        sys.exit("VB-Cable not found. Install it and set CABLE Input as default playback.")
    print(f"-> capturing from device [{idx}]\n")
    return idx


def record(out_path, seconds):
    idx = find_vb_cable()
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                    input=True, input_device_index=idx,
                    frames_per_buffer=FRAMES_PER_BUFFER)

    print(f"Recording {seconds}s -> {out_path}   (play the video now)")
    frames = []
    total = int(SAMPLE_RATE / FRAMES_PER_BUFFER * seconds)
    for i in range(total):
        frames.append(stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False))
        elapsed = (i * FRAMES_PER_BUFFER) / SAMPLE_RATE
        if i > 0 and int(elapsed) % 10 == 0 and (i * FRAMES_PER_BUFFER) % SAMPLE_RATE < FRAMES_PER_BUFFER:
            print(f"  {int(elapsed)}s ...")

    stream.stop_stream(); stream.close(); p.terminate()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(frames))

    kb = os.path.getsize(out_path) / 1024
    print(f"\nSaved {out_path} ({kb:.0f} KB)")
    if kb < 10:
        print("WARNING: file is tiny -- VB-Cable may not be receiving audio. "
              "Check it's set as the default playback device.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python record_to_wav.py <output.wav> [seconds]")
    out = sys.argv[1]
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    record(out, secs)
