"""
MAKE A TEST CLIP — synthesize project claims to a WAV via OpenAI TTS
====================================================================
Generates claim-bearing audio that matches the software-project KB, so you can
demo the live dashboard without a microphone. Edit TEXT to taste.

Usage:
  python make_claims_wav.py                 # -> claims.wav
  python make_claims_wav.py myclip.wav      # custom output name

Then feed it:
  python test_feed_clip.py "claims.wav"
"""

import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# Claims chosen to hit the KB (dashboard ~80% in testing, OAuth complete,
# API in progress, PostgreSQL). Mix of likely VERIFIED / CONTRADICTED, plus
# one logistics line that SHOULD be ignored. Edit freely.
TEXT = (
    "Okay team, quick status. "
    "QA is one hundred percent complete. "
    "We are using MySQL for the main database. "
    "Google login is working in production. "
    "All of the API endpoints are finished and deployed. "
    "The dashboard is fully done and shipped. "
    "Let's take a short break in five minutes."
)

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "claims.wav"
    out = os.path.abspath(out)
    print(f"Synthesizing speech -> {out} …")
    try:
        with client.audio.speech.with_streaming_response.create(
            model="tts-1", voice="alloy", input=TEXT, response_format="wav",
        ) as response:
            response.stream_to_file(out)
    except Exception as e1:
        print(f"  streaming TTS failed ({e1}); trying non-streaming fallback…")
        resp = client.audio.speech.create(
            model="tts-1", voice="alloy", input=TEXT, response_format="wav")
        data = getattr(resp, "content", None)
        if data is None:
            data = resp.read()
        with open(out, "wb") as f:
            f.write(data)

    if os.path.exists(out) and os.path.getsize(out) > 1000:
        print(f"Done ({os.path.getsize(out)} bytes). Now run:  python test_feed_clip.py \"{out}\"")
    else:
        print("ERROR: file was not written (or is empty). Paste the output above.")


if __name__ == "__main__":
    main()
