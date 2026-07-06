#!/usr/bin/env python3
"""
End-to-end test without requiring audio.
Simulates a meeting by sending fake audio chunks directly to the API.
"""

import requests
import json
import time
import wave
import io
from array import array

SERVER = "http://localhost:8000"

def create_fake_audio(duration_sec=2, frequency=440, rate=16000):
    """Generate fake audio data (sine wave)"""
    import math
    samples = array('h')
    for i in range(int(rate * duration_sec)):
        sample = int(32767 * 0.3 * math.sin(2 * math.pi * frequency * i / rate))
        samples.append(sample)
    return samples

def samples_to_wav(samples, rate=16000):
    """Convert sample array to WAV bytes"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()

def test_pipeline():
    print("=" * 60)
    print("🧪 GEPPETTO END-TO-END TEST (No Audio Required)")
    print("=" * 60)
    print()

    # Step 1: Start session
    print("📍 Step 1: Starting a new session...")
    try:
        r = requests.post(f"{SERVER}/api/session/start")
        if r.status_code != 200:
            print(f"❌ Failed to start session: {r.status_code}")
            print(f"   Response: {r.text}")
            return

        data = r.json()
        sid = data["session_id"]
        print(f"✓ Session created: {sid}\n")
    except Exception as e:
        print(f"❌ Error: {e}\n")
        return

    # Step 2: Send test audio chunks
    print("📍 Step 2: Sending fake audio chunks...")
    try:
        for i in range(3):
            print(f"   Sending chunk {i+1}/3...", end=" ")

            # Create fake audio (sine wave)
            fake_audio = create_fake_audio(duration_sec=2, frequency=440)
            wav_bytes = samples_to_wav(fake_audio)

            # Send to server
            r = requests.post(
                f"{SERVER}/api/session/{sid}/chunk",
                data=wav_bytes,
                headers={"Content-Type": "audio/wav"}
            )

            if r.status_code == 202:
                print(f"✓ {r.json()}")
            else:
                print(f"❌ Status {r.status_code}: {r.text}")

            time.sleep(0.5)
        print()
    except Exception as e:
        print(f"❌ Error sending chunks: {e}\n")
        return

    # Step 3: End session
    print("📍 Step 3: Ending session and generating report...")
    try:
        r = requests.post(f"{SERVER}/api/session/{sid}/end")
        if r.status_code == 200:
            data = r.json()
            folder = data.get("folder_name")
            print(f"✓ Session ended")
            print(f"✓ Report saved to: meetings/{folder}\n")
        else:
            print(f"❌ Failed to end session: {r.status_code}\n")
            return
    except Exception as e:
        print(f"❌ Error ending session: {e}\n")
        return

    # Step 4: Check the report
    print("📍 Step 4: Checking generated report...")
    try:
        import os
        report_path = f"/Applications/geppetto-VK/meetings/{folder}/report.json"
        if os.path.exists(report_path):
            with open(report_path) as f:
                report = json.load(f)

            summary = report.get("summary", {})
            print(f"   Total claims detected: {summary.get('total_claims', 0)}")
            print(f"   - Verified: {summary.get('verified', 0)}")
            print(f"   - Contradicted: {summary.get('contradicted', 0)}")
            print(f"   - Unverified: {summary.get('unverified', 0)}")
            print(f"   - Outdated: {summary.get('outdated', 0)}")
            print(f"   - Needs clarification: {summary.get('needs_clarification', 0)}")
            print()
        else:
            print(f"⚠️  Report file not found at {report_path}\n")
    except Exception as e:
        print(f"⚠️  Could not read report: {e}\n")

    print("=" * 60)
    print("✓ TEST COMPLETE")
    print("=" * 60)
    print()
    print("To view the session on the dashboard:")
    print(f"  1. Open http://localhost:8000")
    print(f"  2. Check meeting history (left sidebar)")
    print(f"  3. Click on the latest meeting to view claims & alerts")
    print()

if __name__ == "__main__":
    test_pipeline()
