"""
PHASE 2 (Geppetto 3): FRESHNESS-AWARE CLAIM VALIDATION ENGINE
==============================================================
Validates meeting claims with two paths:

  Temporal path  (Geppetto 3)
    claim → tag_metric() → metric_key + value
         → resolve against SQLite fact timeline
         → VERIFIED / OUTDATED / CONTRADICTED / UNVERIFIED

  Prose path  (Geppetto 2 fallback)
    claim → ChromaDB semantic search → Claude Haiku classification
         → VERIFIED / CONTRADICTED / UNVERIFIED / OUTDATED / NEEDS_CLARIFICATION

Usage:
  from phase2_validator import validate_claim
  result = validate_claim("QA is at 34%", kb_collection, db_path="./facts.db")
"""

import os
import json
import re
from datetime import date
from dotenv import load_dotenv
from anthropic import Anthropic
import chromadb

from facts import (
    METRIC_CATALOG, current as fact_current, history as fact_history,
    find_by_value, _values_match, DEFAULT_DB_PATH
)

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ============================================================================
# LOAD KNOWLEDGE BASE
# ============================================================================

def load_knowledge_base():
    kb_client = chromadb.PersistentClient(path="./chroma_data")
    try:
        collection = kb_client.get_collection(name="project_knowledge")
        return collection
    except Exception as e:
        raise RuntimeError(
            f"Knowledge base not found: {e}\n"
            f"Run phase2_kb_setup.py first!"
        )


# ============================================================================
# METRIC TAGGING  (Geppetto 3)
# ============================================================================

_CATALOG_SUMMARY = "\n".join(
    f"  {k}: {v['description']} (unit: {v['unit']}, hints: {v.get('hints','')})"
    for k, v in METRIC_CATALOG.items()
)

_TAG_PROMPT = """\
Tag the following project meeting claim to a known metric.

CLAIM: "{claim}"

KNOWN METRICS:
{catalog}

If the claim states or implies a specific value for one of these metrics, respond:
{{"metric_key": "the.key", "value": "extracted numeric or text value (no unit suffix)", "unit": "percent|date|text|money|count|bool"}}

Rules:
- For percent: extract just the number (e.g. "82" not "82%")
- For money: extract just the number in dollars (e.g. "500000" for "$500K")
- For date: ISO format YYYY-MM-DD if possible, otherwise the stated value
- If the claim does NOT state a specific value for any metric: {{"metric_key": null}}

Respond ONLY with JSON, no explanation."""


def tag_metric(claim: str) -> dict | None:
    """
    Use Claude Haiku to identify metric_key + extracted value from a claim.
    Returns {"metric_key": str, "value": str, "unit": str} or None.
    Cost: ~1 Haiku call per claim (cached if metric tagging is called twice for same claim).
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": _TAG_PROMPT.format(
                claim=claim, catalog=_CATALOG_SUMMARY
            )}]
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group() if m else raw)
        # Accept any well-formed key — METRIC_CATALOG entries AND auto-discovered keys
        if data.get("metric_key") and isinstance(data["metric_key"], str):
            return data
    except Exception:
        pass
    return None


# ============================================================================
# TEMPORAL RESOLUTION  (Geppetto 3)
# ============================================================================

def _validate_temporal(claim: str, tag: dict, db_path: str, kb_collection) -> dict:
    """
    Resolve claim against the fact timeline.

    Returns a validation result dict compatible with the prose path.
    Adds extra keys: fact_metric, current_value, current_as_of,
                     stated_value, stated_as_of, is_stale, current_value_display.
    """
    metric_key    = tag["metric_key"]
    claimed_value = str(tag.get("value", ""))
    # Safe fallback for auto-discovered keys not in METRIC_CATALOG
    _cat          = METRIC_CATALOG.get(metric_key, {})
    unit          = tag.get("unit") or _cat.get("unit", "text")
    entity        = _cat.get("entity") or metric_key.split(".")[-1].replace("_", " ").title()

    curr = fact_current(metric_key, db_path=db_path)

    # No fact on record → fall back to prose KB
    if curr is None:
        result = _validate_prose(claim, kb_collection)
        result["fact_metric"] = metric_key
        return result

    is_stale      = curr.is_stale()
    is_provisional = curr.provisional
    stale_note     = f" (⚠ last updated {curr.days_old()} days ago)" if is_stale else ""
    prov_note      = " (⏳ unconfirmed — awaiting PM review)" if is_provisional else ""
    curr_display   = curr.value_display()

    # Confidence caps:
    #   provisional numeric → max 0.60 (Medium) until confirmed (G3)
    #   stale              → max 0.55
    is_numeric = unit in ("percent", "count", "money")
    def _cap(base_conf: float) -> float:
        c = base_conf
        if is_stale:
            c = min(c, 0.55)
        if is_provisional and is_numeric:
            c = min(c, 0.60)
        return round(c, 2)

    base = {
        "fact_metric":           metric_key,
        "current_value":         curr.value,
        "current_value_display": curr_display,
        "current_as_of":         curr.as_of,
        "is_stale":              is_stale,
        "is_provisional":        is_provisional,
    }

    # ── Match against current ──────────────────────────────────────────────
    if _values_match(curr.value, claimed_value, unit):
        return {
            **base,
            "category":           "VERIFIED",
            "confidence":         _cap(0.92),
            "supporting_sources": [curr.source],
            "conflicting_sources":[],
            "reasoning":          (
                f"Matches current {entity} value: {curr_display} as of {curr.as_of}"
                f"{stale_note}{prov_note}."
            ),
            "pm_action_suggested": (
                f"Verify: fact may be stale (last update {curr.as_of})." if is_stale
                else f"Confirm fact in Pending tab." if is_provisional
                else "No action needed."
            ),
        }

    # ── Match against history (OUTDATED) ──────────────────────────────────
    old_matches = find_by_value(metric_key, claimed_value, unit=unit, db_path=db_path)
    if old_matches:
        old = old_matches[0]
        return {
            **base,
            "stated_value":        old.value,
            "stated_as_of":        old.as_of,
            "category":            "OUTDATED",
            "confidence":          _cap(0.88),
            "supporting_sources":  [old.source],
            "conflicting_sources": [curr.source],
            "reasoning":           (
                f"{entity} was {old.value_display()} on {old.as_of}; "
                f"current value is {curr_display} as of {curr.as_of}{stale_note}{prov_note}."
            ),
            "pm_action_suggested": (
                f"Update: current {entity} is {curr_display} as of {curr.as_of}."
                + (" Confirm fact in Pending tab." if is_provisional else "")
            ),
        }

    # ── No match anywhere (CONTRADICTED) ──────────────────────────────────
    return {
        **base,
        "category":            "CONTRADICTED",
        "confidence":          _cap(0.85),
        "supporting_sources":  [],
        "conflicting_sources": [curr.source],
        "reasoning":           (
            f"Claim states {entity} is '{claimed_value}' "
            f"but records show {curr_display} as of {curr.as_of}{stale_note}{prov_note}."
        ),
        "pm_action_suggested": (
            f"Correct: {entity} is {curr_display} as of {curr.as_of}."
            + (" Confirm fact in Pending tab." if is_provisional else "")
        ),
    }


# ============================================================================
# PROSE VALIDATION  (Geppetto 2 path — unchanged)
# ============================================================================

def _validate_prose(claim: str, kb_collection) -> dict:
    """ChromaDB + Claude Haiku classification (original Geppetto 2 logic)."""
    search_results = kb_collection.query(query_texts=[claim], n_results=3)
    kb_context = []
    if search_results["documents"] and search_results["documents"][0]:
        for i, doc in enumerate(search_results["documents"][0]):
            metadata = search_results["metadatas"][0][i]
            kb_context.append({
                "source": metadata.get("source", "unknown"),
                "text":   doc
            })

    kb_text = "\n\n".join(
        f"[{item['source']}]\n{item['text']}" for item in kb_context
    )

    prompt = f"""You are validating a claim made in a project meeting against a knowledge base.

CLAIM: "{claim}"

KNOWLEDGE BASE:
{kb_text if kb_text else "(No relevant KB documents found)"}

Classify this claim into ONE category:
1. VERIFIED - Claim aligns with documented sources
2. CONTRADICTED - Claim conflicts with documented sources
3. UNVERIFIED - No supporting or conflicting evidence in KB
4. OUTDATED - Claim was true but newer KB docs contradict it
5. NEEDS_CLARIFICATION - Claim is ambiguous, partial, or temporal

Respond ONLY with valid JSON (no markdown, no extra text):
{{
  "category": "VERIFIED|CONTRADICTED|UNVERIFIED|OUTDATED|NEEDS_CLARIFICATION",
  "confidence": 0.85,
  "supporting_sources": ["source1", "source2"],
  "conflicting_sources": ["source3"],
  "reasoning": "Why this category?",
  "pm_action_suggested": "What should PM do?"
}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        return json.loads(m.group() if m else raw)
    except (json.JSONDecodeError, Exception):
        return {
            "category":            "UNVERIFIED",
            "confidence":          0.5,
            "supporting_sources":  [],
            "conflicting_sources": [],
            "reasoning":           "Could not parse response.",
            "pm_action_suggested": "Manual review needed.",
        }


# ============================================================================
# MAIN VALIDATE FUNCTION
# ============================================================================

def validate_claim(claim: str, kb_collection, db_path: str = None) -> dict:
    """
    Freshness-aware claim validation.

    If db_path is provided and the fact store exists, attempts the temporal path
    first (metric tagging → timeline resolution). Falls back to the prose KB
    path if no metric match is found.

    Args:
        claim:         The claim text to validate.
        kb_collection: ChromaDB collection (always available as fallback).
        db_path:       Path to the SQLite fact store. Optional; enables temporal path.

    Returns:
        Dict with: category, confidence, supporting_sources, conflicting_sources,
        reasoning, pm_action_suggested — plus optional temporal keys
        (fact_metric, current_value, current_as_of, is_stale, …).
    """
    # Temporal path
    if db_path and os.path.exists(db_path):
        tag = tag_metric(claim)
        if tag and tag.get("metric_key"):
            return _validate_temporal(claim, tag, db_path, kb_collection)

    # Prose path (Geppetto 2 fallback)
    return _validate_prose(claim, kb_collection)


# ============================================================================
# EXTRACT CLAIMS
# ============================================================================

def extract_claims(text: str) -> list[str]:
    """Split transcript text into candidate factual claims."""
    sentences = text.replace("? ", "?\n").replace("! ", "!\n").split("\n")
    claim_keywords = [
        "is ", "are ", "has ", "have ", "will ", "completed ", "done ",
        "approved ", "ready ", "finished ", "started ", "blocked ",
        "released ", "deployed ", "scheduled "
    ]
    claims = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > 10 and any(kw in sentence.lower() for kw in claim_keywords):
            claims.append(sentence)
    return claims


# ============================================================================
# PRIORITY
# ============================================================================

def get_priority(category: str, confidence: float) -> str:
    if category == "CONTRADICTED" and confidence > 0.85:
        return "CRITICAL"
    elif category in ("CONTRADICTED", "OUTDATED") and confidence > 0.7:
        return "HIGH"
    elif category in ("NEEDS_CLARIFICATION", "UNVERIFIED"):
        return "MEDIUM"
    else:
        return "LOW"


# ============================================================================
# BATCH VALIDATION
# ============================================================================

def validate_transcript(transcript_text: str, kb_collection,
                         db_path: str = None) -> list[dict]:
    """Validate all claims in a transcript."""
    claims = extract_claims(transcript_text)
    validations = []
    for claim in claims:
        validation = validate_claim(claim, kb_collection, db_path=db_path)
        validation["claim"] = claim
        validation["priority"] = get_priority(
            validation["category"], validation["confidence"]
        )
        validations.append(validation)
    return validations


# ============================================================================
# MAIN (demo)
# ============================================================================

def main():
    print("\n" + "="*60)
    print("PHASE 2: FRESHNESS-AWARE VALIDATION ENGINE")
    print("="*60)

    kb_collection = load_knowledge_base()
    print("✓ Knowledge base loaded")

    db_path = DEFAULT_DB_PATH
    has_facts = os.path.exists(db_path)
    if has_facts:
        print(f"✓ Fact store found at {db_path}")
    else:
        print("⚠  No fact store found — running prose-only mode")
        print("  Run: python fact_extractor.py  to seed the fact store")

    test_claims = [
        "QA is at 82%",          # → VERIFIED  (current)
        "QA is at 34%",          # → OUTDATED  (was true Jun 13)
        "QA is at 15%",          # → OUTDATED  (was true Jun 8)
        "QA is 90% complete",    # → CONTRADICTED (no match)
        "The release is scheduled for June 21",  # → VERIFIED
        "We are using PostgreSQL",               # → VERIFIED
        "The database migration is on track for June 12",  # → fact date match
        "Budget is $500,000",    # → VERIFIED
        "All mobile app tests are passing",      # → prose path
    ]

    print(f"\n{'─'*60}")
    for claim in test_claims:
        result = validate_claim(claim, kb_collection, db_path=db_path if has_facts else None)
        cat    = result["category"]
        conf   = result["confidence"]
        metric = result.get("fact_metric", "prose")
        extra  = ""
        if cat == "OUTDATED" and result.get("current_value"):
            extra = f"  → current: {result.get('current_value_display', result['current_value'])} as of {result.get('current_as_of','?')}"
        stale = "  [STALE]" if result.get("is_stale") else ""
        print(f"\n  Claim:  {claim}")
        print(f"  Result: {cat} ({conf:.0%}) via {metric}{stale}")
        if extra:
            print(f"  {extra}")
        print(f"  Action: {result.get('pm_action_suggested','')}")

    print(f"\n{'='*60}")
    print("✅ Validation engine ready.")


if __name__ == "__main__":
    main()
