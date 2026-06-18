"""
GEPPETTO 3: FACT EXTRACTOR
===========================
Seeds the SQLite fact store with dated facts extracted from the project documents.

Two modes:
  1. seed_known_facts()  — deterministic: seeds the known timeline directly from
                           what the documents contain (fast, no API call, preferred
                           for dev/demo setup).
  2. extract_from_docs() — LLM-assisted: sends doc text to Claude Haiku to extract
                           dated facts (slower, useful for new docs you add later).

Usage:
  python fact_extractor.py            # seeds known facts and shows summary
  python fact_extractor.py --llm      # run LLM extraction on sample docs too
"""

import os
import sys
import json
import re
import argparse
from datetime import date
from dotenv import load_dotenv

from facts import init_db, add_fact, current, history, list_current_facts, METRIC_CATALOG

load_dotenv()
DEFAULT_DB_PATH = "./facts.db"


# ---------------------------------------------------------------------------
# Known facts (derived from reading the project documents)
# ---------------------------------------------------------------------------
# Format: (metric_key, value, unit, as_of, source, entity)
# Dates use 2026 to match the current project timeline (today = 2026-06-17).

KNOWN_FACTS = [
    # QA timeline  — the core Geppetto 3 demo sequence
    ("qa.tests_passed_pct", "15", "percent", "2026-06-08", "standup_notes",         "QA"),
    ("qa.tests_passed_pct", "23", "percent", "2026-06-11", "qa_status_report.md",   "QA"),
    ("qa.tests_passed_pct", "34", "percent", "2026-06-13", "qa_status_report.md",   "QA"),
    ("qa.tests_passed_pct", "82", "percent", "2026-06-14", "qa_status_report.md",   "QA"),

    # Release date
    ("release.date",             "2026-06-21", "date",    "2026-06-01", "SOW_v1.md",                  "Release"),

    # Database
    ("db.engine",                "PostgreSQL",  "text",    "2026-04-15", "architecture_decision_log.md","Database"),
    ("db.migration.deadline",    "2026-06-12",  "date",    "2026-05-01", "architecture_decision_log.md","Database Migration"),

    # Budget
    ("budget.total",             "500000",      "money",   "2026-06-01", "SOW_v1.md",                  "Budget"),
    ("budget.spent",             "420000",      "money",   "2026-06-01", "SOW_v1.md",                  "Budget"),

    # Authentication
    ("auth.status",              "COMPLETE",    "text",    "2026-06-10", "approved_features_v1.md",    "Authentication"),

    # API
    ("api.status",               "IN TESTING",  "text",    "2026-06-10", "approved_features_v1.md",    "API"),

    # Mobile test coverage
    ("mobile.ios.test_pct",      "78",          "percent", "2026-06-13", "qa_status_report.md",        "Mobile iOS"),
    ("mobile.android.test_pct",  "75",          "percent", "2026-06-13", "qa_status_report.md",        "Mobile Android"),

    # Team
    ("team.engineering.headcount","12",         "count",   "2026-06-01", "SOW_v1.md",                  "Engineering Team"),
]


def seed_known_facts(db_path: str = DEFAULT_DB_PATH, overwrite: bool = False) -> int:
    """
    Seed the fact store with KNOWN_FACTS.
    Skips facts that already exist (same metric_key + as_of + value) unless
    overwrite=True (which clears the store first).

    Returns the number of facts inserted.
    """
    init_db(db_path)

    if overwrite:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM facts")
        conn.commit()
        conn.close()
        print("  [!] Cleared existing facts.")

    inserted = 0
    for metric_key, value, unit, as_of, source, entity in KNOWN_FACTS:
        # Skip if this exact (metric, as_of, value) already exists
        existing = [
            f for f in history(metric_key, db_path=db_path)
            if f.as_of == as_of and f.value == value
        ]
        if existing:
            continue
        add_fact(metric_key, value, unit=unit, as_of=as_of,
                 source=source, entity=entity, db_path=db_path)
        inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# LLM-assisted extraction (for new documents)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
You are extracting dated facts from a project document. Return only facts that have a specific, verifiable value — not vague statements.

DOCUMENT NAME: {doc_name}
DOCUMENT TEXT:
{doc_text}

KNOWN METRIC KEYS (map to these if the fact matches):
{catalog}

Extract facts as a JSON array. Each fact:
{{
  "metric_key": "from the catalog above, or null if no match",
  "entity": "short label like QA, Release, Database",
  "value": "the specific value (number without unit, date as YYYY-MM-DD, or short text)",
  "unit": "percent|date|money|text|count|bool",
  "as_of": "YYYY-MM-DD (best estimate from document context; use document date if unclear)",
  "confidence": 0.0-1.0
}}

Return ONLY the JSON array, no other text. If no facts found, return [].
"""


def extract_from_docs(docs: dict, db_path: str = DEFAULT_DB_PATH,
                      min_confidence: float = 0.75) -> int:
    """
    Run Claude Haiku over a dict of {doc_name: doc_text} to extract dated facts.
    Returns the number of facts inserted.
    """
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception as e:
        print(f"  [!] Cannot initialize Anthropic client: {e}")
        return 0

    catalog_summary = "\n".join(
        f"  {k}: {v['description']} (unit: {v['unit']})"
        for k, v in METRIC_CATALOG.items()
    )

    inserted = 0
    for doc_name, doc_text in docs.items():
        print(f"  Extracting from {doc_name}…", end=" ", flush=True)
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(
                    doc_name=doc_name,
                    doc_text=doc_text[:3000],
                    catalog=catalog_summary
                )}]
            )
            raw = response.content[0].text.strip()
            # Extract JSON array
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            facts = json.loads(m.group()) if m else []
        except Exception as e:
            print(f"failed ({e})")
            continue

        doc_inserted = 0
        for item in facts:
            if item.get("confidence", 0) < min_confidence:
                continue
            mk = item.get("metric_key")
            if not mk or mk not in METRIC_CATALOG:
                continue
            val = item.get("value", "")
            if not val:
                continue
            unit   = item.get("unit", METRIC_CATALOG[mk]["unit"])
            as_of  = item.get("as_of", date.today().isoformat())
            entity = item.get("entity", METRIC_CATALOG[mk]["entity"])
            # Avoid duplicates
            existing = [
                f for f in history(mk, db_path=db_path)
                if f.as_of == as_of and f.value == str(val)
            ]
            if not existing:
                add_fact(mk, val, unit=unit, as_of=as_of,
                         source=doc_name, entity=entity, db_path=db_path)
                doc_inserted += 1

        inserted += doc_inserted
        print(f"{doc_inserted} facts inserted")

    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Geppetto 3 fact extractor")
    parser.add_argument("--db",       default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--overwrite", action="store_true",    help="Clear and re-seed")
    parser.add_argument("--llm",       action="store_true",    help="Also run LLM extraction on sample docs")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("GEPPETTO 3 — FACT STORE SEEDER")
    print("="*60)

    # Step 1: seed known facts
    print("\n[1] Seeding known facts…")
    n = seed_known_facts(db_path=args.db, overwrite=args.overwrite)
    print(f"    {n} facts inserted.")

    # Step 2: optionally run LLM extraction
    if args.llm:
        print("\n[2] Running LLM extraction on sample documents…")
        from phase2_kb_setup import SAMPLE_DOCUMENTS
        n2 = extract_from_docs(SAMPLE_DOCUMENTS, db_path=args.db)
        print(f"    {n2} additional facts extracted.")

    # Summary
    print("\n[Summary] Current fact store:")
    facts = list_current_facts(db_path=args.db)
    if not facts:
        print("  (empty)")
    else:
        for f in facts:
            stale = " ⚠ STALE" if f.is_stale() else ""
            print(f"  {f.metric_key:<35} {f.value_display():<15} as of {f.as_of}  [{f.source}]{stale}")

    print("\n  QA timeline (qa.tests_passed_pct):")
    for f in history("qa.tests_passed_pct", db_path=args.db):
        print(f"    {f.as_of}  {f.value_display():<10}  from {f.source}")

    print("\n✅ Done. Run 'python phase3_server_realtime.py' to start the server.\n")


if __name__ == "__main__":
    main()
