

py -3.12 -c "
import pyaudio
p = pyaudio.PyAudio()
try:
    p.is_format_supported(16000.0, input_device=1, input_channels=1, input_format=pyaudio.paInt16)
    print('16000 Hz supported')
except:
    print('16000 Hz NOT supported')
"