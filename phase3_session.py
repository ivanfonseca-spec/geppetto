"""
PHASE 3 (real-time): LIVE SESSION / TRANSCRIPTION CORE
======================================================
The per-meeting engine the real-time server (phase3_server_realtime.py) drives.
For each incoming audio chunk it:

  1. (VAD backstop) skips near-silent chunks to avoid cost + Whisper hallucination
  2. transcribes the chunk with whisper-1  — NO priming
  3. appends to the per-session rolling transcript
  4. detects only NEW claims (phase3_claims.IncrementalClaimDetector)
     — now returns Claim objects with pre-tagged metric_key/value/unit (Option B)
  5. validates all claims in this chunk CONCURRENTLY via asyncio.gather (Option B)
     — passes pre-supplied tag to validate_claim() to skip the tag_metric() call
  6. returns alert objects (schema = REQUIREMENTS_REALTIME.md §7.1 + temporal fields)

Option B changes vs. the original:
  - ingest_chunk() and finalize() are now async
  - validation of a chunk's claims runs concurrently (asyncio.gather)
  - pre-supplied tags from the merged detect+tag call are passed to validate_claim()
  - server call-site must await these methods directly (no run_in_threadpool wrapper)
  - shared-state mutation (self.alerts, self.claim_count) is guarded by threading.Lock
"""

import io
import os
import time
import wave
import array
import uuid
import asyncio
import hashlib
import tempfile
import threading
from datetime import datetime, timezone

from phase3_claims import IncrementalClaimDetector, Claim
from phase2_validator import validate_claim, get_priority

WHISPER_MODEL = "whisper-1"
LANGUAGE = "en"
SILENCE_RMS = 150        # backstop VAD gate
WHISPER_RETRIES = 2      # NFR-10: retry transient API failures with backoff


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def rms_of_wav(wav_bytes):
    """RMS amplitude of a 16-bit WAV (mono or multi-channel). None if unknown format."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            width, ch = w.getsampwidth(), w.getnchannels()
            frames = w.readframes(w.getnframes())
        if width != 2:
            return None
        s = array.array("h"); s.frombytes(frames)
        if ch > 1:
            s = s[0::ch]
        if not s:
            return 0.0
        acc = 0
        for x in s:
            acc += x * x
        return (acc / len(s)) ** 0.5
    except Exception:
        return None


def confidence_label(c):
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "Low"
    if c >= 0.8:
        return "High"
    if c >= 0.6:
        return "Medium"
    return "Low"


def transcribe_chunk(openai_client, wav_bytes, retries=WHISPER_RETRIES):
    """whisper-1, no priming, with exponential backoff on transient failures."""
    last = None
    for attempt in range(retries + 1):
        fd, tmp = tempfile.mkstemp(suffix=".wav"); os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(wav_bytes)
            with open(tmp, "rb") as f:
                return openai_client.audio.transcriptions.create(
                    model=WHISPER_MODEL, file=f, language=LANGUAGE).text.strip()
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
        finally:
            os.remove(tmp)
    raise last


# ----------------------------------------------------------------------------
# live session
# ----------------------------------------------------------------------------
class LiveSession:
    def __init__(self, kb_collection, openai_client, anthropic_client=None,
                 session_id=None, recovery_dir=None, db_path=None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.status = "listening"
        self.rolling_transcript = ""
        self.alerts = []
        self.claim_count = 0
        self.kb = kb_collection
        self.openai = openai_client
        self.db_path = db_path
        self.detector = IncrementalClaimDetector(client=anthropic_client)
        self._tlock = threading.Lock()   # guards self.alerts + self.claim_count
        self.recovery_path = (os.path.join(recovery_dir, f".live_{self.id}.txt")
                              if recovery_dir else None)

    async def ingest_chunk(self, wav_bytes):
        """
        Process one audio chunk. Returns list of NEW alert dicts (may be empty).
        Async: STT runs in a thread; this chunk's claims are validated concurrently.
        Raises on persistent transcription failure so caller can warn and continue.
        """
        self.status = "processing"
        rms = rms_of_wav(wav_bytes)
        if rms is not None and rms < SILENCE_RMS:
            self.status = "listening"
            return []
        try:
            text = await asyncio.to_thread(transcribe_chunk, self.openai, wav_bytes)
        finally:
            self.status = "listening"

        if text:
            self.rolling_transcript = (self.rolling_transcript + " " + text).strip()
            self._flush_recovery()

        claims = self.detector.feed_text(text)
        if not claims:
            return []

        # Validate all claims in this chunk concurrently
        results = await asyncio.gather(
            *[asyncio.to_thread(self._validate_one, c) for c in claims],
            return_exceptions=False,
        )
        return [a for a in results if a is not None]

    async def finalize(self):
        """
        Flush any buffered trailing claims, mark ended.
        Returns (transcript, alerts).
        """
        trailing = self.detector.flush()
        if trailing:
            await asyncio.gather(
                *[asyncio.to_thread(self._validate_one, c) for c in trailing],
                return_exceptions=False,
            )
        self.status = "ended"
        self._clear_recovery()
        return self.rolling_transcript, self.alerts

    def _flush_recovery(self):
        if not self.recovery_path:
            return
        try:
            with open(self.recovery_path, "w", encoding="utf-8") as f:
                f.write(self.rolling_transcript)
        except Exception:
            pass

    def _clear_recovery(self):
        if self.recovery_path and os.path.exists(self.recovery_path):
            try:
                os.remove(self.recovery_path)
            except Exception:
                pass

    def state(self):
        return {
            "session_id":        self.id,
            "started_at":        self.started_at,
            "status":            self.status,
            "rolling_transcript": self.rolling_transcript,
            "alerts":            self.alerts,
            "claim_count":       self.claim_count,
        }

    # ----- internals (called from threads via asyncio.to_thread) -----

    def _validate_one(self, claim: Claim):
        """
        Validate a single Claim. Called in a thread pool via asyncio.to_thread.
        Passes the pre-supplied tag to skip the tag_metric() round-trip (Option B).
        Returns an alert dict, or None on failure.
        """
        try:
            result = validate_claim(
                claim.text, self.kb,
                db_path=self.db_path,
                tag=claim.tag,
            )
        except Exception:
            return None

        alert = self._build_alert(claim.text, result)

        with self._tlock:
            self.alerts.append(alert)
            self.claim_count += 1

        return alert

    def _build_alert(self, claim_text, result):
        conf = result.get("confidence", 0.5)
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            conf_f = 0.5

        alert = {
            "claim_id":           hashlib.sha1(claim_text.encode()).hexdigest()[:10],
            "claim_text":         claim_text,
            "category":           result.get("category", "UNVERIFIED"),
            "confidence":         confidence_label(conf),
            "confidence_score":   conf_f,
            "evidence":           self._evidence(claim_text, result),
            "suggested_response": result.get("pm_action_suggested", ""),
            "reasoning":          result.get("reasoning", ""),
            "priority":           get_priority(result.get("category", "UNVERIFIED"), conf_f),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
        }

        for key in ("fact_metric", "current_value", "current_value_display",
                    "current_as_of", "stated_value", "stated_as_of",
                    "is_stale", "is_provisional"):
            if key in result:
                alert[key] = result[key]

        return alert

    def _evidence(self, claim_text, result):
        sources = list(dict.fromkeys(
            (result.get("supporting_sources") or []) +
            (result.get("conflicting_sources") or [])
        ))
        evidence = []
        for src in sources:
            if not src:
                continue
            try:
                res = self.kb.query(
                    query_texts=[claim_text], n_results=1,
                    where={"source": src}
                )
                if res["documents"] and res["documents"][0]:
                    snippet = res["documents"][0][0][:150].replace("\n", " ").strip()
                    evidence.append({"source": src, "snippet": snippet})
                else:
                    evidence.append({"source": src, "snippet": ""})
            except Exception:
                evidence.append({"source": src, "snippet": ""})
        return evidence
