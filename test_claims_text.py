"""
TEXT TEST — claim detection + validation, no audio
==================================================
Runs the REAL incremental claim detector (phase3_claims) and validator
(phase2_validator) against typed text, scored against your knowledge base.
Fastest way to see claims get categorized (VERIFIED / CONTRADICTED / …) and to
tune claim wording — no Whisper, no VB-Cable, no server.

Usage:
  python test_claims_text.py                 # uses a built-in demo transcript
  python test_claims_text.py "meetings\\some\\transcript.txt"   # your own text
"""

import sys

from phase2_validator import load_knowledge_base, validate_claim, get_priority
from phase3_claims import IncrementalClaimDetector

# Project-style claims that should hit the software KB (edit freely):
DEMO = (
    "QA is 100% complete. "
    "We are using MySQL for the main database. "
    "The dashboard is fully done and shipped. "
    "Google login is working. "
    "All the API endpoints are finished and deployed. "
    "Let's take a short break."          # logistics -> should be IGNORED
)


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = DEMO

    print("Loading knowledge base…")
    kb = load_knowledge_base()
    det = IncrementalClaimDetector()

    # feed the whole text, then flush the trailing buffered sentence
    claims = det.feed_text(text) + det.flush()
    print(f"\nDetected {len(claims)} claim(s):\n" + "-" * 60)

    for c in claims:
        r = validate_claim(c, kb)
        conf = r.get("confidence", 0)
        pr = get_priority(r.get("category", "UNVERIFIED"),
                          float(conf) if isinstance(conf, (int, float)) else 0.5)
        print(f"[{r.get('category','?'):<18}] {pr:<8} \"{c}\"")
        if r.get("conflicting_sources"):
            print(f"     conflicts: {', '.join(r['conflicting_sources'])}")
        if r.get("supporting_sources"):
            print(f"     supports:  {', '.join(r['supporting_sources'])}")
        print(f"     why: {r.get('reasoning','')}")
        print(f"     suggest: {r.get('pm_action_suggested','')}\n")

    if not claims:
        print("(No project-state claims found — try text with statuses, %, dates,"
              " ownership, or decisions.)")


if __name__ == "__main__":
    main()
