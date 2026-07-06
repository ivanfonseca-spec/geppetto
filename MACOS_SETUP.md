# Geppetto on macOS — Complete Setup Guide

This guide walks you through setting up Geppetto for real-time meeting fact-checking on macOS.

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| macOS | 10.14+ | 12.0+ (Monterey+) |
| Python | 3.8 | 3.10+ |
| RAM | 4GB | 8GB+ |
| Disk | 500MB | 2GB |
| Internet | Required (API calls) | Broadband |

---

## Part 1: Prerequisites ✅

### 1.1 Install Python (if not already installed)

```bash
# Check if Python is installed
python3 --version

# If not, install via Homebrew:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python3
```

### 1.2 Get API Keys

You need two API keys:

**OpenAI API Key** (for Whisper speech-to-text):
1. Go to https://platform.openai.com/account/api-keys
2. Click "Create new secret key"
3. Copy and save it (you won't see it again)

**Anthropic API Key** (for Claude claim validation):
1. Go to https://console.anthropic.com/account/keys
2. Click "Create Key"
3. Copy and save it

Store these somewhere safe — you'll need them later.

### 1.3 Clone the Repository

```bash
cd ~/Desktop  # or wherever you want the project
git clone https://github.com/YOUR_USERNAME/geppetto-VK.git
cd geppetto-VK
```

---

## Part 2: Python Environment Setup

### 2.1 Create Virtual Environment (Recommended)

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# You should see (venv) at the start of your terminal prompt
```

### 2.2 Install Dependencies

```bash
pip install --upgrade pip

pip install fastapi "uvicorn[standard]" openai anthropic chromadb python-dotenv pyaudio
```

**If PyAudio installation fails:**

```bash
# Install system audio library first
brew install portaudio

# Then retry pip
pip install pyaudio
```

### 2.3 Create `.env` File

```bash
# Create the file
touch .env

# Edit it with your favorite editor and add:
OPENAI_API_KEY=sk-proj-YOUR_OPENAI_KEY_HERE
ANTHROPIC_API_KEY=sk-ant-api03-YOUR_ANTHROPIC_KEY_HERE
```

**⚠️ IMPORTANT:** Never commit `.env` to Git. It's already in `.gitignore`.

---

## Part 3: macOS Audio Configuration 🎤

This is the **most important** part for real-time audio capture.

### 3.1 Grant Microphone Permission

1. **Open System Settings**
   - Apple menu → System Settings (or System Preferences)

2. **Navigate to Privacy & Security**
   - Left sidebar → Privacy & Security

3. **Go to Microphone**
   - Scroll down to "Microphone"
   - Click it

4. **Grant access to Python**
   - Look for "Python" or "Terminal" in the list
   - If not present, click "+" to add it
   - Navigate to `/usr/local/bin/python3` (or wherever your Python is)
   - Click "Open" to add it

5. **Verify it's enabled**
   - You should see a checkmark next to Python
   - If you're using a virtual environment, you may need to add that Python too

---

### 3.2 Set Default Input Device (Microphone)

1. **Open System Settings → Sound**
2. **Click the "Input" tab**
3. **Select "MacBook Pro Microphone"**
   - (Not Bluetooth headsets for now — they can be flaky)

4. **Test microphone input level**
   - The level meter should respond when you speak
   - Adjust your microphone position until you see green bars when speaking

---

### 3.3 Optional: Set Up VB-Cable (For System Audio)

If you want to capture audio **from apps** (Zoom, Teams meetings, etc.):

**Download & Install:**
1. Visit https://vb-audio.com/Cable/
2. Download the macOS version
3. Run the installer
4. **Restart your Mac** (required!)

**Configure:**
1. Open System Settings → Sound
2. Click "Output" tab
3. Select "VB-Cable" as your output device
4. Everything you play will now go through VB-Cable
5. The audio streamer will capture it

**Note:** After this, you may not hear audio from your speakers. To fix:
- Use headphones, OR
- Set output back to "MacBook Pro Speakers" and use a multi-output device

---

## Part 4: Test Your Setup

### 4.1 Test Microphone Access

```bash
cd /Applications/geppetto-VK

# Run the microphone test
python3 test_microphone.py
```

**Expected output:**
```
🎤 Testing Microphone Audio Capture
==================================================
Found 7 audio devices:

[0] → INPUT | MacBook Pro Microphone
...

✓ Microphone opened successfully

Recording for 5 seconds...
SPEAK NOW:

[1/75] RMS:      0 
[2/75] RMS:    450 ███
...

✓ Recording complete
Maximum audio level detected: 750
✓ Audio levels look good!
```

If you get permission errors:
- Check System Settings → Privacy & Security → Microphone
- Make sure Python is enabled

### 4.2 Test End-to-End (No Audio Required)

This test doesn't need a microphone — it simulates everything:

```bash
# Terminal 1: Start the server
uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000

# Terminal 2: Run the test
python3 test_end_to_end.py
```

**Expected output:**
```
============================================================
🧪 GEPPETTO END-TO-END TEST (No Audio Required)
============================================================

📍 Step 1: Starting a new session...
✓ Session created: a1b2c3d4e5f6

📍 Step 2: Sending fake audio chunks...
   Sending chunk 1/3... ✓ {'alerts': 0}
   Sending chunk 2/3... ✓ {'alerts': 0}
   Sending chunk 3/3... ✓ {'alerts': 0}

📍 Step 3: Ending session and generating report...
✓ Session ended
✓ Report saved to: meetings/2026-07-04_16-45-55

✓ TEST COMPLETE
```

---

## Part 5: Run Geppetto

### 5.1 Terminal Setup

You need **3 terminal windows/tabs**:

**Terminal 1: Server**
```bash
cd /Applications/geppetto-VK
uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
```

Expected output:
```
Uvicorn running on http://127.0.0.1:8000
```

**Terminal 2: Open Dashboard**
```bash
# Just open in your browser:
open http://localhost:8000

# Or paste into any browser:
http://localhost:8000
```

**Terminal 3: Audio Streamer (after starting meeting)**
```bash
cd /Applications/geppetto-VK

# First, start a meeting in the dashboard
# Copy the exact command shown at the bottom, e.g.:
python3 phase1_audio_streaming.py --server http://localhost:8000 --session a1b2c3d4

# Paste it here and run
```

---

### 5.2 Run a Meeting

1. **Start the server** (Terminal 1)
2. **Open dashboard** (Terminal 2)
3. **Click "Start live meeting"** on the dashboard
4. **Copy the command** shown at the bottom (includes session ID)
5. **Paste & run it** in Terminal 3
6. **Speak into your microphone**
   - Speak clearly: "We're 100% done with testing"
   - Watch for alerts in the dashboard

**Expected experience:**
- 🌊 Waveform animates at bottom as you speak
- 📝 Transcript appears on the right
- ⚠️ Alert cards appear in center if claims contradict KB
- Color-coded: Green (verified), Red (contradicted), Yellow (unverified)

---

## Part 6: Troubleshooting

### Issue: "Microphone permission denied"

**Solution:**
```bash
# System Settings → Privacy & Security → Microphone
# Make sure Python appears and is enabled
# If not:
# 1. Click "+"
# 2. Navigate to /usr/local/bin/python3
# 3. Click "Open"
```

### Issue: No audio input detected

**Check:**
```bash
python3 test_microphone.py
```

If RMS levels are 0:
- Mic is not working or muted
- Wrong input device selected (System Settings → Sound → Input)
- Mic needs permission (see above)

### Issue: "HTTP Error 404: Not Found" when streaming

**This means:** Session ID mismatch

**Fix:**
1. Click "End meeting" on dashboard
2. Click "Start live meeting" again
3. **Copy the EXACT command** shown at bottom
4. Run that exact command in terminal
5. Don't modify the session ID

### Issue: No alerts appearing

**Check:**
1. Mic is capturing (test with `test_microphone.py`)
2. Server is running (Terminal 1 shows no errors)
3. Dashboard shows "listening" status
4. Session IDs match (streamer session = dashboard URL)
5. Speak clearly and wait 3-5 seconds for processing

### Issue: "Unknown session" errors

**This happens when:**
- Server restarted (sessions are lost)
- Using old session ID from previous meeting
- Terminal 1 (server) crashed

**Solution:**
1. Restart the server: `Ctrl+C` then run the uvicorn command again
2. Start a NEW meeting on dashboard
3. Use the NEW command shown

---

## Part 7: Common Tasks

### Check if Server is Running

```bash
curl http://localhost:8000
```

Should return HTML (the dashboard).

### Restart Everything

```bash
# Terminal 1: Stop server
Ctrl+C

# Terminal 3: Stop streamer  
Ctrl+C

# Then restart:
# Terminal 1: uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
# Terminal 2: open http://localhost:8000
# Click "Start live meeting" on dashboard
# Copy & run the command in Terminal 3
```

### View Meeting Reports

```bash
# Check saved meetings
ls -la meetings/

# View a specific meeting
open meetings/2026-07-04_16-45-55/report.html
```

### Update to Latest Code

```bash
git pull origin main
pip install --upgrade -r requirements.txt
```

---

## Part 8: Next Steps

Once everything is working:

1. **Test with real audio** — Run a 5-minute test meeting
2. **Check performance** — Monitor costs (Whisper + Claude calls)
3. **Customize KB** — Add your project's facts to improve accuracy
4. **Schedule recurring meetings** — Set up automated testing

---

## Support

If you get stuck:

1. **Check logs** — Terminal 1 and 3 show detailed output
2. **Run test suite** — `python3 test_end_to_end.py`
3. **Review this guide** — Most issues are covered above
4. **Check GitHub issues** — geppetto/issues/

---

## Quick Reference Checklist

- [ ] Python 3.8+ installed
- [ ] API keys in `.env`
- [ ] Dependencies installed (`pip install ...`)
- [ ] Microphone permission granted (System Settings)
- [ ] Input device set to MacBook Pro Microphone
- [ ] Microphone test passes (`python3 test_microphone.py`)
- [ ] End-to-end test passes (`python3 test_end_to_end.py`)
- [ ] Server runs without errors
- [ ] Dashboard loads at http://localhost:8000
- [ ] Can start/end meetings on dashboard
- [ ] Audio streamer connects with correct session ID

**You're ready to go! 🚀**

