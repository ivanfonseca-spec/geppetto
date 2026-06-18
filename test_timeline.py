"""
GEPPETTO 3: TIMELINE VALIDATION TEST
=====================================
Verifies the core Geppetto 3 temporal truth logic without requiring
audio, OpenAI, or a live server.

Tests:
  T1  Current value → VERIFIED
  T2  Superseded value → OUTDATED (with current surfaced)
  T3  Never-seen value → CONTRADICTED
  T4  Staleness guard (metric not updated in >7 days → is_stale=True)
  T5  No fact on record → falls through (UNVERIFIED via prose or no metric tag)

Run:
  py test_timeline.py
"""

import os, sys
from datetime import date, timedelta
from facts import init_db, add_fact, current, history, find_by_value, _values_match

import tempfile
TEST_DB = os.path.join(tempfile.gettempdir(), "test_facts_g3.db")

def clean():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def setup():
    """Seed a QA timeline in a fresh test DB."""
    clean()
    init_db(TEST_DB)
    today = date.today()
    add_fact("qa.tests_passed_pct", "15", unit="percent", as_of=(today - timedelta(days=9)).isoformat(),  source="standup_notes",       entity="QA", db_path=TEST_DB)
    add_fact("qa.tests_passed_pct", "23", unit="percent", as_of=(today - timedelta(days=6)).isoformat(),  source="qa_status_report.md",  entity="QA", db_path=TEST_DB)
    add_fact("qa.tests_passed_pct", "34", unit="percent", as_of=(today - timedelta(days=4)).isoformat(),  source="qa_status_report.md",  entity="QA", db_path=TEST_DB)
    add_fact("qa.tests_passed_pct", "82", unit="percent", as_of=(today - timedelta(days=3)).isoformat(),  source="qa_status_report.md",  entity="QA", db_path=TEST_DB)
    # Stale metric: last updated 10 days ago
    add_fact("budget.total",        "500000", unit="money", as_of=(today - timedelta(days=10)).isoformat(), source="SOW_v1.md", entity="Budget", db_path=TEST_DB)

PASS = "✅ PASS"
FAIL = "❌ FAIL"

def run():
    setup()
    results = []

    # T1: Current value → should match current, is_stale=False
    curr = current("qa.tests_passed_pct", db_path=TEST_DB)
    assert curr is not None, "current() returned None"
    match_current = _values_match(curr.value, "82", "percent")
    t1 = PASS if (match_current and not curr.is_stale()) else FAIL
    results.append(("T1 Current=VERIFIED (82% matches latest)", t1, f"current={curr.value}, stale={curr.is_stale()}"))

    # T2: Old value → should find historical match, current should be 82
    old_matches = find_by_value("qa.tests_passed_pct", "34", unit="percent", db_path=TEST_DB)
    t2 = PASS if (old_matches and old_matches[0].value == "34" and curr.value == "82") else FAIL
    results.append(("T2 Old value=OUTDATED (34% matches history)", t2,
                    f"found={old_matches[0].value if old_matches else None}, current={curr.value}"))

    # T3: Never-seen value → no historical match
    unseen = find_by_value("qa.tests_passed_pct", "90", unit="percent", db_path=TEST_DB)
    t3 = PASS if not unseen else FAIL
    results.append(("T3 Unseen value=CONTRADICTED (90% not in history)", t3,
                    f"matches={[f.value for f in unseen]}"))

    # T4: Staleness guard — budget.total was updated 10 days ago
    budget = current("budget.total", db_path=TEST_DB)
    t4 = PASS if (budget and budget.is_stale()) else FAIL
    results.append(("T4 Staleness (budget 10 days old → is_stale=True)", t4,
                    f"days_old={budget.days_old() if budget else 'N/A'}, is_stale={budget.is_stale() if budget else 'N/A'}"))

    # T5: Unknown metric → no current
    unknown = current("nonexistent.metric", db_path=TEST_DB)
    t5 = PASS if unknown is None else FAIL
    results.append(("T5 Unknown metric → None (falls to prose)", t5, f"current={unknown}"))

    # T6: History ordering — verify newest first
    hist = history("qa.tests_passed_pct", db_path=TEST_DB)
    t6 = PASS if (hist and hist[0].value == "82" and hist[-1].value == "15") else FAIL
    results.append(("T6 History ordering (newest first: 82→34→23→15)", t6,
                    f"order={[f.value for f in hist]}"))

    # T7: Numeric tolerance — 81 vs 82 should NOT match (within 1 abs point)
    near_match = _values_match("82", "81", "percent")
    t7 = PASS if near_match else FAIL  # tolerance is ±1, so 81 is within range
    results.append(("T7 Tolerance (81 vs 82 within ±1 point → match)", t7, f"match={near_match}"))

    # T8: Strict mismatch — 80 vs 82 should NOT match
    strict_no_match = _values_match("82", "80", "percent")
    t8 = PASS if not strict_no_match else FAIL
    results.append(("T8 No-match (80 vs 82 exceeds ±1 point → no match)", t8, f"match={strict_no_match}"))

    # Print results
    print("\n" + "="*65)
    print("GEPPETTO 3 — TIMELINE VALIDATION TEST")
    print("="*65)
    all_pass = True
    for name, status, detail in results:
        print(f"\n  {status}  {name}")
        print(f"       {detail}")
        if status == FAIL:
            all_pass = False

    print("\n" + "="*65)
    if all_pass:
        print("✅ ALL TESTS PASSED — temporal truth logic is working.\n")
    else:
        print("❌ SOME TESTS FAILED — check details above.\n")

    clean()
    return all_pass


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
