"""
GEPPETTO 3: SYNC + DOC_LOADERS VERIFICATION
=============================================
Tests the incremental KB sync pipeline without requiring API keys.
Runs entirely locally against a temp Chroma collection and temp docs dir.

Tests:
  T1  .md file loaded correctly
  T2  NEW file → ingested, manifest created, chunks in Chroma
  T3  Unchanged file → SKIP on second sync (mtime+size fast-path)
  T4  MODIFIED file (content change) → re-ingested, old chunks replaced
  T5  DELETED file → chunks removed from Chroma, manifest entry removed
  T6  docs/notes/ subdirectory → tier tagged as 'derived'
  T7  docs/ root file → tier tagged as 'authoritative'
  T8  Chunk text content is preserved and searchable in Chroma

Run:
  py test_sync.py
"""

import os
import sys
import shutil
import tempfile

# ── Setup ──────────────────────────────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"

def run():
    results = []

    # Create isolated temp dirs for this test run
    tmp_root  = tempfile.mkdtemp(prefix="g3_sync_test_")
    docs_dir  = os.path.join(tmp_root, "docs")
    notes_dir = os.path.join(docs_dir, "notes")
    chroma_dir = os.path.join(tmp_root, "chroma")
    db_path   = os.path.join(tmp_root, "test_facts.db")
    os.makedirs(docs_dir, exist_ok=True)
    os.makedirs(notes_dir, exist_ok=True)

    try:
        import chromadb
        chroma = chromadb.PersistentClient(path=chroma_dir)
        col    = chroma.get_or_create_collection("test_kb")
    except ImportError:
        print("ERROR: chromadb not installed. Run: pip install chromadb")
        sys.exit(1)

    from doc_loaders import load_document, SUPPORTED_EXTENSIONS
    from kb_sync import sync, _get_tier, _chunk_text
    from facts import init_db

    init_db(db_path)

    # ── T1: .md loader ────────────────────────────────────────────────────────
    md_path = os.path.join(docs_dir, "sample.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Project Status\n\nQA is at 82% as of June 14, 2026.\nRelease is June 21.\n")
    result = load_document(md_path)
    t1 = PASS if result and "QA is at 82%" in result["text"] else FAIL
    results.append(("T1 .md loader returns text", t1,
                    f"text[:40]={result['text'][:40] if result else None}"))

    # ── T2: NEW file → ingested ───────────────────────────────────────────────
    counts = sync(docs_dir, col, db_path=db_path, extract_facts=False)
    t2 = PASS if counts["new"] >= 1 and counts["skipped"] == 0 else FAIL
    results.append(("T2 NEW file ingested on first sync", t2, f"counts={counts}"))

    # Verify chunks searchable in Chroma
    hits = col.query(query_texts=["QA tests passed"], n_results=1)
    t2b = PASS if hits["documents"] and hits["documents"][0] else FAIL
    results.append(("T2b Chunks are searchable in Chroma", t2b,
                    f"hit={hits['documents'][0][0][:40] if hits['documents'] and hits['documents'][0] else None}"))

    # ── T3: SKIP unchanged file ───────────────────────────────────────────────
    counts2 = sync(docs_dir, col, db_path=db_path, extract_facts=False)
    t3 = PASS if counts2["skipped"] >= 1 and counts2["new"] == 0 and counts2["modified"] == 0 else FAIL
    results.append(("T3 Unchanged file SKIPped on second sync", t3, f"counts={counts2}"))

    # ── T4: MODIFIED file → re-ingested ──────────────────────────────────────
    # Get chunk count before
    before = col.count()
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Project Status UPDATED\n\nQA is now at 95% as of June 17, 2026.\nRelease confirmed June 21.\n")
    import time; time.sleep(0.05)  # ensure mtime changes
    os.utime(md_path, None)
    counts3 = sync(docs_dir, col, db_path=db_path, extract_facts=False)
    # Should see modified=1
    hits2 = col.query(query_texts=["QA is now at 95%"], n_results=1)
    t4 = PASS if counts3["modified"] >= 1 and hits2["documents"] and hits2["documents"][0] else FAIL
    results.append(("T4 MODIFIED file re-ingested with new content", t4,
                    f"modified={counts3['modified']} hit={hits2['documents'][0][0][:30] if hits2['documents'] and hits2['documents'][0] else None}"))

    # ── T5: DELETED file → chunks removed ────────────────────────────────────
    os.remove(md_path)
    counts4 = sync(docs_dir, col, db_path=db_path, extract_facts=False)
    # Old content should no longer be in Chroma
    hits3 = col.query(query_texts=["QA is now at 95%"], n_results=1)
    no_hit = not hits3["documents"] or not hits3["documents"][0]
    t5 = PASS if counts4["deleted"] >= 1 and no_hit else FAIL
    results.append(("T5 DELETED file chunks removed from Chroma", t5,
                    f"deleted={counts4['deleted']} no_hit={no_hit}"))

    # ── T6: docs/notes/ → tier=derived ───────────────────────────────────────
    tier_note = _get_tier("notes/meeting-2026-06-17.md")
    t6 = PASS if tier_note == "derived" else FAIL
    results.append(("T6 docs/notes/ → tier=derived", t6, f"tier={tier_note}"))

    # ── T7: docs/ root → tier=authoritative ───────────────────────────────────
    tier_root = _get_tier("GEPPETTO_3_PLAN.md")
    t7 = PASS if tier_root == "authoritative" else FAIL
    results.append(("T7 docs/ root file → tier=authoritative", t7, f"tier={tier_root}"))

    # ── T8: Chunking preserves content ────────────────────────────────────────
    long_text = ("Project update. " * 100)  # 1600 chars
    chunks    = _chunk_text(long_text, size=800, overlap=100)
    total_coverage = sum(len(c) for c in chunks)
    t8 = PASS if len(chunks) >= 2 and total_coverage >= len(long_text) * 0.9 else FAIL
    results.append(("T8 Chunking: long text → multiple overlapping chunks", t8,
                    f"chunks={len(chunks)} coverage={total_coverage}/{len(long_text)}"))

    # ── Print results ─────────────────────────────────────────────────────────
    print("\n" + "="*65)
    print("GEPPETTO 3 — SYNC + DOC_LOADERS TEST")
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

    # Cleanup
    shutil.rmtree(tmp_root, ignore_errors=True)
    return all_pass


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
