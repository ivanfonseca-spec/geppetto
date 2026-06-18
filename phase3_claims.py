"""
PHASE 3 (real-time): INCREMENTAL CLAIM DETECTOR
===============================================
Detects NEW factual claims in newly transcribed text only, so the validator
isn't re-run on the whole rolling transcript every chunk (FR-6).

Option B change: claim detection and metric tagging are now merged into a
single Haiku call, returning a Claim object that carries both the claim text
AND the metric tag (metric_key/value/unit). This saves one round-trip per
chunk compared to the previous two-call design.

Callers that only need the text string use claim.text as before.
Callers that want the pre-supplied tag pass claim.tag to validate_claim().

Usage:
    from phase3_claims import IncrementalClaimDetector
    claims = det.feed_text(chunk_transcript)   # list[Claim], only unseen
    for c in claims:
        print(c.text, c.tag)   # tag may be None if not detected
    leftover = det.flush()
"""

import os
import re
import json
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher

DETECTOR_MODEL = "claude-haiku-4-5-20251001"

# Sentence boundary: split after . ! ? followed by whitespace.
_SENT_END = re.compile(r"(?<=[.!?])\s+")

# Force-process the buffer if it grows past this without a sentence end.
_MAX_BUFFER_CHARS = 400


# ---------------------------------------------------------------------------
# Claim dataclass — carries text + optional pre-tagged metric info
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    text: str
    tag: Optional[dict] = field(default=None)
    # tag shape when present: {"metric_key": str, "value": str, "unit": str}


# ---------------------------------------------------------------------------
# Merged detect + tag prompt
# ---------------------------------------------------------------------------

CLAIM_DETECTION_PROMPT = '''You extract verifiable FACTUAL CLAIMS about project state from a snippet of meeting speech, and tag each claim to a known metric if possible.

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

For each claim, also attempt to tag it to a metric key using these known metrics:
{catalog}

Return ONLY JSON, no markdown:
{{"claims": [
  {{
    "text": "verbatim claim text",
    "metric_key": "the.key or null if no match",
    "value": "extracted numeric or text value (no unit suffix), or null",
    "unit": "percent|date|text|money|count|bool or null"
  }}
]}}

Rules for tagging:
- For percent: extract just the number (e.g. "82" not "82%")
- For money: extract just the number in dollars (e.g. "500000" for "$500K")
- For date: ISO format YYYY-MM-DD if possible, otherwise the stated value
- If a claim has no clear metric match, set metric_key/value/unit to null
- Use an empty list if there are no factual claims

MEETING TEXT:
"""{text}"""'''


# ---------------------------------------------------------------------------
# Incremental claim detector
# ---------------------------------------------------------------------------

class IncrementalClaimDetector:
    def __init__(self, client=None, model=DETECTOR_MODEL, sim_threshold=0.88,
                 max_buffer_chars=_MAX_BUFFER_CHARS):
        self._client = client
        self.model = model
        self.sim_threshold = sim_threshold
        self.max_buffer_chars = max_buffer_chars
        self._buffer = ""
        self._seen_hashes = set()
        self._seen_norm = []
        self._catalog_summary = None   # lazily built

    # ----- public API -------------------------------------------------------

    def feed_text(self, new_text) -> list:
        """Append newly transcribed text; return list of new Claim objects."""
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

    def flush(self) -> list:
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

    def _catalog(self):
        """Build a compact catalog summary for injection into the prompt."""
        if self._catalog_summary is None:
            try:
                from facts import METRIC_CATALOG
                lines = [
                    f"  {k}: {v['description']} (unit: {v['unit']})"
                    for k, v in METRIC_CATALOG.items()
                ]
                self._catalog_summary = "\n".join(lines)
            except Exception:
                self._catalog_summary = "  (no catalog available)"
        return self._catalog_summary

    def _llm_detect(self, text) -> list:
        prompt = CLAIM_DETECTION_PROMPT.format(text=text, catalog=self._catalog())
        try:
            resp = self._client_or_init().messages.create(
                model=self.model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
        except Exception:
            return []
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group() if m else raw)
            raw_claims = data.get("claims", [])
        except (json.JSONDecodeError, AttributeError):
            return []

        claims = []
        for item in raw_claims:
            if isinstance(item, str):
                # Fallback: old-style plain string (shouldn't happen but be safe)
                claims.append(Claim(text=item.strip()))
            elif isinstance(item, dict):
                text_val = (item.get("text") or "").strip()
                if not text_val:
                    continue
                mk = item.get("metric_key")
                val = item.get("value")
                unit = item.get("unit")
                tag = ({"metric_key": mk, "value": str(val), "unit": unit or "text"}
                       if mk and val else None)
                claims.append(Claim(text=text_val, tag=tag))
        return claims

    @staticmethod
    def _normalize(claim_text: str) -> str:
        norm = claim_text.lower().replace("%", " percent ")
        norm = re.sub(r"[^a-z0-9 ]", "", norm)
        return re.sub(r"\s+", " ", norm).strip()

    def _is_dup(self, norm: str) -> bool:
        h = hashlib.sha1(norm.encode()).hexdigest()
        if h in self._seen_hashes:
            return True
        for prev in self._seen_norm:
            if SequenceMatcher(None, norm, prev).ratio() >= self.sim_threshold:
                return True
        return False

    def _dedup(self, claims: list) -> list:
        out = []
        for c in claims:
            norm = self._normalize(c.text)
            if not norm or self._is_dup(norm):
                continue
            self._seen_hashes.add(hashlib.sha1(norm.encode()).hexdigest())
            self._seen_norm.append(norm)
            out.append(c)
        return out
