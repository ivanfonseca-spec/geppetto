"""
GEPPETTO 3: PROVISIONAL + PENDING QUEUE TEST
==============================================
Tests the human-in-the-loop fact review pipeline without requiring API keys.

Tests:
  T1  add_fact(provisional=True) → fact in store, provisional=True
  T2  Provisional fact has reduced confidence cap in validator
  T3  confirm_fact() → provisional cleared, confirmed_at set
  T4  reject_fact() → provisional fact deleted from store
  T5  add_pending() → in pending queue with status='pending'
  T6  accept_pending() → moves to fact store, pending status='accepted'
  T7  reject_pending() → status='rejected', NOT in fact store
  T8  Rejected pending does not re-surface (rejection memory check)
  T9  Idempotent add_fact: same (metric_key, as_of, value, source) → no duplicate

Run:
  py test_pending.py
"""

import os
import sys
import tempfile
from datetime import date

PASS = "PASS"
FAIL = "FAIL"


def run():
    import tempfile
    db_path = os.path.join(tempfile.gettempdir(), "test_pending_g3.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    from facts import (
        init_db, add_fact, current, list_provisional,
        confirm_fact, reject_fact,
        add_pending, list_pending, accept_pending, reject_pending,
    )
    from kb_sync import _already_rejected

    init_db(db_path)
    today = date.today().isoformat()
    results = []

    # ── T1: provisional fact stored ───────────────────────────────────────────
    f1 = add_fact("risk.launch_delay", "HIGH", unit="text", as_of=today,
                  source="qa_report.md", entity="Risk", provisional=True, db_path=db_path)
    t1 = PASS if f1.provisional and current("risk.launch_delay", db_path=db_path) is not None else FAIL
    results.append(("T1 Provisional fact stored and retrievable", t1,
                    f"provisional={f1.provisional} fact_id={f1.fact_id[:8]}"))

    # ── T2: provisional → in list_provisional ────────────────────────────────
    provisionals = list_provisional(db_path=db_path)
    t2 = PASS if any(p.fact_id == f1.fact_id for p in provisionals) else FAIL
    results.append(("T2 Provisional fact appears in list_provisional()", t2,
                    f"provisional_count={len(provisionals)}"))

    # ── T3: confirm_fact → cleared ────────────────────────────────────────────
    ok = confirm_fact(f1.fact_id, db_path=db_path)
    curr = current("risk.launch_delay", db_path=db_path)
    t3 = PASS if ok and curr and not curr.provisional and curr.confirmed_at else FAIL
    results.append(("T3 confirm_fact() clears provisional, sets confirmed_at", t3,
                    f"ok={ok} provisional={curr.provisional if curr else '?'} confirmed_at={curr.confirmed_at[:10] if curr and curr.confirmed_at else None}"))

    # ── T4: reject_fact → deleted ─────────────────────────────────────────────
    f2 = add_fact("risk.scope_creep", "MEDIUM", unit="text", as_of=today,
                  source="pm_notes.md", entity="Risk", provisional=True, db_path=db_path)
    ok4 = reject_fact(f2.fact_id, db_path=db_path)
    gone = current("risk.scope_creep", db_path=db_path)
    t4 = PASS if ok4 and gone is None else FAIL
    results.append(("T4 reject_fact() removes provisional fact", t4,
                    f"ok={ok4} still_exists={gone is not None}"))

    # ── T5: add_pending ───────────────────────────────────────────────────────
    pid = add_pending("task.new_auth_flow", "IN_PROGRESS", unit="text", as_of=today,
                      source="meeting-notes.md", entity="Task", tier="derived", db_path=db_path)
    pending = list_pending(db_path=db_path, status="pending")
    t5 = PASS if any(p["pending_id"] == pid for p in pending) else FAIL
    results.append(("T5 add_pending() stored in pending queue", t5,
                    f"pending_id={pid[:8]} queue_size={len(pending)}"))

    # ── T6: accept_pending → in fact store ───────────────────────────────────
    fact = accept_pending(pid, db_path=db_path)
    curr6 = current("task.new_auth_flow", db_path=db_path)
    accepted = list_pending(db_path=db_path, status="accepted")
    t6 = PASS if fact and curr6 and not curr6.provisional and any(p["pending_id"] == pid for p in accepted) else FAIL
    results.append(("T6 accept_pending() moves to fact store", t6,
                    f"in_store={curr6 is not None} accepted_status={any(p['pending_id']==pid for p in accepted)}"))

    # ── T7: reject_pending → NOT in store ────────────────────────────────────
    pid2 = add_pending("task.old_api_refactor", "PLANNED", unit="text", as_of=today,
                       source="meeting-notes.md", entity="Task", tier="derived", db_path=db_path)
    ok7 = reject_pending(pid2, db_path=db_path)
    no_fact = current("task.old_api_refactor", db_path=db_path)
    rejected = list_pending(db_path=db_path, status="rejected")
    t7 = PASS if ok7 and no_fact is None and any(p["pending_id"] == pid2 for p in rejected) else FAIL
    results.append(("T7 reject_pending() stays out of fact store", t7,
                    f"ok={ok7} in_store={no_fact is not None} status=rejected"))

    # ── T8: rejection memory (_already_rejected) ──────────────────────────────
    already = _already_rejected("task.old_api_refactor", "meeting-notes.md", db_path=db_path)
    not_rej = _already_rejected("task.old_api_refactor", "different_source.md", db_path=db_path)
    t8 = PASS if already and not not_rej else FAIL
    results.append(("T8 _already_rejected() detects prior rejection by (key, source)", t8,
                    f"same_source={already} different_source={not_rej}"))

    # ── T9: idempotent add_fact ───────────────────────────────────────────────
    f_a = add_fact("qa.tests_passed_pct", "82", unit="percent", as_of=today,
                   source="qa_report.md", entity="QA", db_path=db_path)
    f_b = add_fact("qa.tests_passed_pct", "82", unit="percent", as_of=today,
                   source="qa_report.md", entity="QA", db_path=db_path)
    t9 = PASS if f_a.fact_id == f_b.fact_id else FAIL
    results.append(("T9 Idempotent add_fact: same tuple → no duplicate", t9,
                    f"id_a={f_a.fact_id[:8]} id_b={f_b.fact_id[:8]} same={f_a.fact_id==f_b.fact_id}"))

    # ── Print results ─────────────────────────────────────────────────────────
    print("\n" + "="*65)
    print("GEPPETTO 3 — PROVISIONAL + PENDING QUEUE TEST")
    print("="*65)
    all_pass = True
    for name, status, detail in results:
        icon = "OK " if status == PASS else "ERR"
        print(f"\n  {icon}  {name}")
        print(f"       {detail}")
        if status == FAIL:
            all_pass = False

    print("\n" + "="*65)
    if all_pass:
        print("ALL TESTS PASSED\n")
    else:
        print("SOME TESTS FAILED\n")

    if os.path.exists(db_path):
        os.remove(db_path)
    return all_pass


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
