#!/usr/bin/env python3
import pyaudio
import numpy as np
from array import array

RATE = 16000
CHUNK = 1024
DURATION = 5  # seconds

print("🎤 Testing Microphone Audio Capture\n")
print("=" * 50)

# List devices
p = pyaudio.PyAudio()
print(f"Found {p.get_device_count()} audio devices:\n")

for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    is_input = info['maxInputChannels'] > 0
    marker = "→ INPUT" if is_input else "  output"
    print(f"[{i}] {marker} | {info['name']}")

print("\n" + "=" * 50)
print(f"Opening default input device (16kHz, mono)...\n")

try:
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                    frames_per_buffer=CHUNK)

    print(f"✓ Microphone opened successfully\n")
    print(f"Recording for {DURATION} seconds...")
    print("SPEAK NOW:\n")

    max_rms = 0
    for i in range(0, RATE // CHUNK * DURATION):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frame = array('h')
        frame.frombytes(data)

        # Calculate RMS
        rms = np.sqrt(np.mean(np.array(frame, dtype=float) ** 2))
        max_rms = max(max_rms, rms)

        # Visual bar
        bar_len = int(rms / 150)
        bar = "█" * min(bar_len, 50)
        print(f"[{i+1}/{RATE // CHUNK * DURATION}] RMS: {rms:6.0f} {bar}")

    stream.stop_stream()
    stream.close()

    print(f"\n✓ Recording complete")
    print(f"Maximum audio level detected: {max_rms:.0f}")

    if max_rms < 100:
        print("⚠️  WARNING: Very low audio levels - check microphone volume!")
    elif max_rms < 500:
        print("⚠️  WARNING: Low audio levels - speak louder")
    else:
        print("✓ Audio levels look good!")

except Exception as e:
    print(f"✗ Error: {e}")
finally:
    p.terminate()
