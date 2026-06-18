import pyaudio
p = pyaudio.PyAudio()
try:
    p.is_format_supported(16000.0, input_device=1, input_channels=1, input_format=pyaudio.paInt16)
    print("16000 Hz: SUPPORTED")
except Exception as e:
    print(f"16000 Hz: NOT supported — {e}")

try:
    p.is_format_supported(44100.0, input_device=1, input_channels=1, input_format=pyaudio.paInt16)
    print("44100 Hz: SUPPORTED")
except Exception as e:
    print(f"44100 Hz: NOT supported — {e}")
p.terminate()
