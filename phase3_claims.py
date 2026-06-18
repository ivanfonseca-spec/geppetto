"""
PHASE 3 (real-time): INCREMENTAL CLAIM DETECTOR
===============================================
Detects NEW factual claims in newly transcribed text only, so the validator
isn't re-run on the whole rolling transcript every chunk (FR-6).

Improvements over phase2's keyword `extract_claims`:
  - LLM-based detection with explicit positive/negative examples (resolves the
    NFR-5 / §6.3 "claim detection spec" gap).
  - Sentence-aware buffering so a claim split across chunk boundaries is only
    processed once it's complete.
  - Dedup by normalized hash AND fuzzy similarity, so re-transcription jitter
    ("QA is 80% done" vs "QA is 80 percent done") doesn't double-alert
    (the brittle-hash risk flagged in review).

Single-claim validation already exists: phase2_validator.validate_claim().

Usage:
    from phase3_claims import IncrementalClaimDetector
    det = IncrementalClaimDetector()
    new_claims = det.feed_text(chunk_transcript)   # list[str], only unseen claims
    ...
    leftover = det.flush()                          # call on session end
"""

import os
import re
import json
import hashlib
from difflib import SequenceMatcher

DETECTOR_MODEL = "claude-haiku-4-5-20251001"

# Sentence boundary: split after . ! ? followed by whitespace.
_SENT_END = re.compile(r"(?<=[.!?])\s+")

# Force-process the buffer if it grows past this without a sentence end
# (Whisper usually punctuates, but protects against a long unpunctuated run).
_MAX_BUFFER_CHARS = 400

CLAIM_DETECTION_PROMPT = '''You extract verifiable FACTUAL CLAIMS about project state from a snippet of meeting speech.

A CLAIM is a checkable assertion about one of: status/progress, a percentage,
a date/deadline, ownership, a dependency, an approval, a decision, or a numeric
fact (budget, counts, versions).

FLAG these (examples):
- "QA is 80% done"
- "the API dependency is resolved"
- "we approved this on May 15"
- "the release ships June 21"
- "Maria owns the migration"
- "we're using PostgreSQL for the main database"

IGNORE these (examples):
- greetings / logistics: "let's take a break", "can everyone hear me?"
- opinions without facts: "I think this looks good", "that's exciting"
- questions: "are we on track?"
- hypotheticals / future intentions: "if we slip we could add people"
- filler / backchannel: "yeah", "right", "okay so"

Return ONLY JSON, no markdown:
{{"claims": ["verbatim claim 1", "verbatim claim 2"]}}
Use an empty list if there are no factual claims.

MEETING TEXT:
"""{text}"""'''


class IncrementalClaimDetector:
    def __init__(self, client=None, model=DETECTOR_MODEL, sim_threshold=0.88,
                 max_buffer_chars=_MAX_BUFFER_CHARS):
        self._client = client          # lazily created if None (see _client_or_init)
        self.model = model
        self.sim_threshold = sim_threshold
        self.max_buffer_chars = max_buffer_chars
        self._buffer = ""
        self._seen_hashes = set()
        self._seen_norm = []

    # ----- public API -------------------------------------------------------
    def feed_text(self, new_text):
        """Append newly transcribed text; return list of NEW claims (may be empty)."""
        if not new_text:
            return []
        sep = " " if self._buffer and not self._buffer.endswith(" ") else ""
        self._buffer += sep + new_text.strip()

        parts = _SENT_END.split(self._buffer)
        completed = parts[:-1]
        remainder = parts[-1]

        # Force-flush an over-long unpunctuated buffer.
        if not completed and len(self._buffer) >= self.max_buffer_chars:
            completed, remainder = [self._buffer], ""

        if not completed:
            return []

        self._buffer = remainder
        text = " ".join(s.strip() for s in completed if s.strip())
        if not text:
            return []
        return self._dedup(self._llm_detect(text))

    def flush(self):
        """Process any trailing buffered text. Call once when the session ends."""
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return []
        return self._dedup(self._llm_detect(text))

    @property
    def seen_count(self):
        return len(self._seen_norm)

    # ----- internals --------------------------------------------------------
    def _client_or_init(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        return self._client

    def _llm_detect(self, text):
        prompt = CLAIM_DETECTION_PROMPT.format(text=text)
        try:
            resp = self._client_or_init().messages.create(
                model=self.model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
        except Exception:
            return []  # detection failure is non-fatal; next chunk retries (NFR-10)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group() if m else raw)
            claims = data.get("claims", [])
            return [c.strip() for c in claims if isinstance(c, str) and c.strip()]
        except (json.JSONDecodeError, AttributeError):
            return []

    @staticmethod
    def _normalize(claim):
        norm = claim.lower().replace("%", " percent ")
        norm = re.sub(r"[^a-z0-9 ]", "", norm)
        return re.sub(r"\s+", " ", norm).strip()

    def _is_dup(self, norm):
        h = hashlib.sha1(norm.encode()).hexdigest()
        if h in self._seen_hashes:
            return True
        for prev in self._seen_norm:
            if SequenceMatcher(None, norm, prev).ratio() >= self.sim_threshold:
                return True
        return False

    def _dedup(self, claims):
        out = []
        for c in claims:
            norm = self._normalize(c)
            if not norm or self._is_dup(norm):
                continue
            self._seen_hashes.add(hashlib.sha1(norm.encode()).hexdigest())
            self._seen_norm.append(norm)
            out.append(c)
        return out
