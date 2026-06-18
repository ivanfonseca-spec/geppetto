import pyaudio
from array import array

p = pyaudio.PyAudio()

# List all input devices
print("Input devices:")
for i in range(p.get_device_count()):
    d = p.get_device_info_by_index(i)
    if d["maxInputChannels"] > 0:
        print(f"  [{i}] {d['name']} — {d['defaultSampleRate']} Hz")

print("\nRecording 3 seconds from default input...")
stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                input=True, frames_per_buffer=480)

max_rms = 0
for i in range(100):
    raw = stream.read(480, exception_on_overflow=False)
    frame = array("h"); frame.frombytes(raw)
    rms = (sum(x*x for x in frame) / len(frame)) ** 0.5 if frame else 0
    if rms > max_rms:
        max_rms = rms
    if i % 10 == 0:
        print(f"  rms={rms:.1f}  max_so_far={max_rms:.1f}")

stream.stop_stream(); stream.close(); p.terminate()
print(f"\nPeak RMS: {max_rms:.1f}")
if max_rms < 10:
    print("PROBLEM: mic not capturing audio — check Windows input device settings")
else:
    print("OK: mic is working")
