# Geppetto — Quick Start Guide

Get Geppetto running in 5 minutes.

## Prerequisites ✅

- macOS 10.14+
- Python 3.8+
- API keys (OpenAI + Anthropic)
- Microphone

## Step-by-Step (5 minutes)

### 1. Configure Microphone Permission (1 min)

```bash
# System Settings → Privacy & Security → Microphone
# ✅ Enable Python access
# ✅ Set Input device to "MacBook Pro Microphone"
```

### 2. Create `.env` File (1 min)

```bash
cd /Applications/geppetto-VK

# Create .env file with your API keys
cat > .env << EOF
OPENAI_API_KEY=sk-proj-YOUR_KEY_HERE
ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
EOF
```

### 3. Install Dependencies (2 min)

```bash
pip install fastapi "uvicorn[standard]" openai anthropic chromadb python-dotenv pyaudio
```

### 4. Test Microphone (Optional)

```bash
python3 test_microphone.py
# Should show audio levels when you speak
```

### 5. Run Geppetto (1 min)

**Terminal 1 - Start Server:**
```bash
cd /Applications/geppetto-VK
uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
```

**Terminal 2 - Open Dashboard:**
```bash
open http://localhost:8000
```

**Terminal 3 - Run Audio Streamer:**
```bash
# After clicking "Start live meeting" on dashboard:
python3 phase1_audio_streaming.py --server http://localhost:8000 --session [SESSION_ID]
```

**Speak into microphone** and watch for alerts! 🎤

---

## Testing Without Audio

To test the system without real audio:

```bash
python3 test_end_to_end.py
```

This will:
- ✅ Create a session
- ✅ Send fake audio chunks
- ✅ Generate a meeting report
- ✅ Verify the API works

---

## Troubleshooting

| Issue | Solution |
|---|---|
| "Permission denied" on microphone | System Settings → Privacy & Security → Microphone → Enable Python |
| "Unknown session" error | Use exact command shown at bottom of dashboard |
| No alerts appearing | Check microphone is working: `python3 test_microphone.py` |
| Server crashes | Restart: `Ctrl+C` then run uvicorn command again |

---

## Full Documentation

For complete setup details, see: **MACOS_SETUP.md**

---

## What Geppetto Does

Geppetto listens to your meetings and checks claims against your knowledge base:

```
You say:    "We're 100% done with testing"
KB says:    "82% done"
Alert:      ❌ CONTRADICTED (Red card appears)

You say:    "QA has approved release"
KB says:    "Waiting for QA sign-off"
Alert:      ❌ CONTRADICTED

You say:    "Meeting is Tuesday"
KB says:    (No info about meetings)
Alert:      ⓘ UNVERIFIED (Yellow card appears)
```

---

## Questions?

Check **MACOS_SETUP.md** for detailed troubleshooting and configuration.
