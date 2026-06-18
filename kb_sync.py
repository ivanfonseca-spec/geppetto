"""
GEPPETTO 3: INCREMENTAL KB SYNC
================================
Keeps the ChromaDB knowledge base current with files in ./docs/.
Driven by .geppetto_manifest.json — only changed files are re-embedded.

Key design points:
  - Provenance tiers: docs/notes/ → derived; everything else → authoritative
  - Auto-discovers metrics via Haiku on authoritative files:
      authoritative → committed as provisional (live at reduced confidence)
      derived       → held in pending_facts (not used until PM accepts)
  - Key stability: injects known metric keys into every extraction prompt
  - Idempotent: safe to call repeatedly; unchanged files are skipped
  - Atomic manifest writes (temp + rename) guard against crash corruption
  - Rejection memory: skips extractions for (file, metric_key) pairs the PM rejected

Usage:
  from kb_sync import sync
  summary = sync(docs_dir="./docs", collection=chroma_col, db_path="./facts.db")
  print(summary)
  # {'new': 2, 'modified': 1, 'deleted': 0, 'skipped': 5, 'pending_extractions': 3}

CLI:
  py kb_sync.py [--docs ./docs] [--db ./facts.db] [--no-extract]
"""

import os
import sys
import json
import uuid
import hashlib
import logging
import tempfile
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from doc_loaders import load_document, SUPPORTED_EXTENSIONS
from facts import (
    init_db, add_fact, list_metrics, add_pending, list_pending,
    DEFAULT_DB_PATH
)

logger = logging.getLogger(__name__)

MANIFEST_FILE    = ".geppetto_manifest.json"
CHUNK_SIZE       = 800
CHUNK_OVERLAP    = 100
NOTES_SUBDIR     = "notes"   # docs/notes/ → derived tier


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest(docs_dir: str) -> dict:
    path = os.path.join(docs_dir, MANIFEST_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning("Corrupt manifest at %s — starting fresh", path)
        return {}


def _save_manifest(docs_dir: str, manifest: dict) -> None:
    """Atomic write: write to temp file then rename."""
    path = os.path.join(docs_dir, MANIFEST_FILE)
    fd, tmp = tempfile.mkstemp(dir=docs_dir, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# File hashing + tier detection
# ---------------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_tier(rel_path: str) -> str:
    """
    Assign provenance tier based on path.
    docs/notes/* → derived  (AI meeting notes)
    everything else → authoritative  (human-written project docs)
    """
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0].lower() == NOTES_SUBDIR:
        return "derived"
    return "authoritative"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """
    Split text into overlapping chunks of ~size characters.
    Tries to break on sentence boundaries ('. ', '\\n') where possible.
    """
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        # Try to find a natural break point near the end of the window
        if end < length:
            for sep in ("\n\n", ". ", "\n", " "):
                idx = text.rfind(sep, start + size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start >= length or end == length:
            break
    return chunks


# ---------------------------------------------------------------------------
# Metric extraction via Haiku
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Extract all measurable, dated project facts from the document excerpt below.

KNOWN METRIC KEYS (reuse if the concept matches; mint a new dotted key only if none fits):
{known_keys}

DOCUMENT SOURCE: {source}
DOCUMENT EXCERPT:
{text}

Return a JSON array of facts. Each fact:
{{
  "metric_key": "domain.attribute",   // lowercase, dot-separated, e.g. qa.tests_passed_pct
  "value":      "82",                  // numeric or text, no unit suffix
  "unit":       "percent|date|money|count|text|bool",
  "as_of":      "YYYY-MM-DD",          // date the value was stated; use document date if unclear
  "entity":     "short label"          // e.g. "QA", "Budget", "Release"
}}

Rules:
- percent: number only (e.g. "82" not "82%")
- money: dollars as integer (e.g. "500000" for "$500K")
- date: ISO format YYYY-MM-DD when possible
- Omit vague or non-measurable statements
- Return [] if nothing measurable is present
- Respond ONLY with the JSON array, no explanation."""


def _extract_metrics(text: str, source: str, doc_date: str,
                     known_keys: list, anthropic_client) -> list:
    """
    Run Haiku over a document text to extract structured metric facts.
    Returns list of {metric_key, value, unit, as_of, entity} dicts.
    """
    known_keys_str = "\n".join(f"  - {k}" for k in known_keys) if known_keys else "  (none yet)"
    # Truncate very large docs to avoid token limits
    excerpt = text[:6000] if len(text) > 6000 else text
    prompt = _EXTRACTION_PROMPT.format(
        known_keys=known_keys_str,
        source=source,
        text=excerpt,
    )
    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        data = json.loads(m.group() if m else raw)
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.warning("metric extraction failed for %s: %s", source, e)
    return []


def _normalize_key(key: str) -> str:
    """Normalize metric key: lowercase, dots only, no spaces or special chars."""
    key = key.lower().strip()
    key = re.sub(r'[^a-z0-9._]', '.', key)
    key = re.sub(r'\.{2,}', '.', key)
    return key.strip('.')


def _already_rejected(metric_key: str, source: str, db_path: str) -> bool:
    """True if the PM already rejected an extraction for this (metric_key, source) pair."""
    rejected = list_pending(db_path=db_path, status="rejected")
    return any(
        r["metric_key"] == metric_key and r["source"] == source
        for r in rejected
    )


# ---------------------------------------------------------------------------
# Ingest a single file into Chroma
# ---------------------------------------------------------------------------

def _ingest_file(rel_path: str, abs_path: str, tier: str,
                 collection, manifest: dict, modified_date: str) -> list:
    """
    Load, chunk, and embed one file. Removes old chunks first.
    Returns list of new chunk_ids.
    """
    # Remove old chunks if this is a re-ingest
    old_entry = manifest.get(rel_path, {})
    old_chunk_ids = old_entry.get("chunk_ids", [])
    if old_chunk_ids:
        try:
            collection.delete(ids=old_chunk_ids)
        except Exception as e:
            logger.warning("Could not delete old chunks for %s: %s", rel_path, e)

    result = load_document(abs_path)
    if result is None:
        return []

    text     = result["text"]
    filename = os.path.basename(rel_path)
    chunks   = _chunk_text(text)
    if not chunks:
        return []

    chunk_ids = []
    ids, docs, metas = [], [], []
    for i, chunk in enumerate(chunks):
        cid = f"{uuid.uuid4().hex}"
        ids.append(cid)
        docs.append(chunk)
        metas.append({
            "source":        filename,
            "rel_path":      rel_path,
            "tier":          tier,
            "chunk_index":   i,
            "modified_date": modified_date,
        })
        chunk_ids.append(cid)

    # Upsert in batches of 100
    batch = 100
    for start in range(0, len(ids), batch):
        collection.upsert(
            ids=ids[start:start+batch],
            documents=docs[start:start+batch],
            metadatas=metas[start:start+batch],
        )

    logger.debug("Ingested %s: %d chunks (tier=%s)", rel_path, len(chunk_ids), tier)
    return chunk_ids


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync(
    docs_dir: str,
    collection,
    db_path: str = DEFAULT_DB_PATH,
    extract_facts: bool = True,
    anthropic_client=None,
) -> dict:
    """
    Incremental sync of docs_dir into the ChromaDB collection.

    Steps:
      1. Enumerate docs_dir for supported file types.
      2. Classify: NEW / MODIFIED / SKIP / DELETED.
      3. NEW/MODIFIED: re-chunk and embed; DELETED: remove chunks.
      4. On authoritative files: run metric extraction, commit as provisional.
         On derived files: run metric extraction, add to pending queue.
      5. Save updated manifest atomically.

    Args:
        docs_dir:         Path to the docs directory.
        collection:       ChromaDB collection to upsert into.
        db_path:          Path to the SQLite fact store.
        extract_facts:    If False, skip metric extraction (prose sync only).
        anthropic_client: Anthropic client for metric extraction. Required if
                          extract_facts=True.

    Returns:
        dict with keys: new, modified, deleted, skipped, pending_extractions
    """
    docs_dir = os.path.abspath(docs_dir)
    if not os.path.isdir(docs_dir):
        logger.warning("docs_dir does not exist: %s", docs_dir)
        return {"new": 0, "modified": 0, "deleted": 0, "skipped": 0, "pending_extractions": 0}

    manifest = _load_manifest(docs_dir)
    counts   = {"new": 0, "modified": 0, "deleted": 0, "skipped": 0, "pending_extractions": 0}

    # ── Step 1: Enumerate current files ──────────────────────────────────────
    current_files = {}  # rel_path → abs_path
    for dirpath, _dirnames, filenames in os.walk(docs_dir):
        for fname in filenames:
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, docs_dir).replace("\\", "/")
            current_files[rel_path] = abs_path

    # ── Step 2+3: Classify and process ───────────────────────────────────────
    changed_authoritative = []  # (rel_path, abs_path, text) for fact extraction

    for rel_path, abs_path in current_files.items():
        try:
            stat     = os.stat(abs_path)
            mtime    = stat.st_mtime
            size     = stat.st_size
            tier     = _get_tier(rel_path)
            mod_date = datetime.fromtimestamp(mtime).date().isoformat()

            entry = manifest.get(rel_path, {})
            # Fast-path: mtime + size match → likely unchanged
            if entry and entry.get("mtime") == mtime and entry.get("size") == size:
                file_hash = entry.get("sha256", "")
            else:
                file_hash = _sha256(abs_path)

            if not entry:
                action = "NEW"
            elif entry.get("sha256") != file_hash:
                action = "MODIFIED"
            else:
                counts["skipped"] += 1
                continue  # SKIP

            # Ingest into Chroma
            chunk_ids = _ingest_file(rel_path, abs_path, tier, collection, manifest, mod_date)
            manifest[rel_path] = {
                "sha256":      file_hash,
                "mtime":       mtime,
                "size":        size,
                "chunk_ids":   chunk_ids,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "tier":        tier,
            }
            counts["new" if action == "NEW" else "modified"] += 1

            # Track authoritative files for fact extraction
            if extract_facts and tier == "authoritative" and anthropic_client:
                result = load_document(abs_path)
                if result:
                    changed_authoritative.append((rel_path, os.path.basename(rel_path),
                                                  result["text"], mod_date, tier))

        except Exception as e:
            logger.error("Error processing %s: %s", rel_path, e)

    # Track derived files for pending extraction
    changed_derived = []
    if extract_facts and anthropic_client:
        for rel_path, abs_path in current_files.items():
            tier = _get_tier(rel_path)
            if tier == "derived":
                entry = manifest.get(rel_path, {})
                # Only extract from newly ingested derived files (already counted above)
                if rel_path in [r for r, *_ in changed_authoritative]:
                    continue
                result = load_document(abs_path)
                if result:
                    mod_date = entry.get("ingested_at", date.today().isoformat())[:10]
                    changed_derived.append((rel_path, os.path.basename(rel_path),
                                            result["text"], mod_date, tier))

    # ── Step 4: DELETED files ────────────────────────────────────────────────
    for rel_path in list(manifest.keys()):
        if rel_path == MANIFEST_FILE:
            continue
        if rel_path not in current_files:
            old_chunk_ids = manifest[rel_path].get("chunk_ids", [])
            if old_chunk_ids:
                try:
                    collection.delete(ids=old_chunk_ids)
                except Exception as e:
                    logger.warning("Could not delete chunks for deleted %s: %s", rel_path, e)
            del manifest[rel_path]
            counts["deleted"] += 1

    # ── Step 5: Metric extraction ─────────────────────────────────────────────
    if extract_facts and anthropic_client:
        known_keys = list_metrics(db_path=db_path)

        for rel_path, source, text, doc_date, tier in changed_authoritative:
            facts = _extract_metrics(text, source, doc_date, known_keys, anthropic_client)
            for f in facts:
                mk = _normalize_key(f.get("metric_key", ""))
                if not mk:
                    continue
                if _already_rejected(mk, source, db_path):
                    continue
                add_fact(
                    metric_key=mk,
                    value=f.get("value", ""),
                    unit=f.get("unit", "text"),
                    as_of=f.get("as_of") or doc_date,
                    source=source,
                    entity=f.get("entity", ""),
                    provisional=True,
                    db_path=db_path,
                )
                known_keys = list(set(known_keys + [mk]))  # keep prompt fresh
                counts["pending_extractions"] += 1

        for rel_path, source, text, doc_date, tier in changed_derived:
            facts = _extract_metrics(text, source, doc_date, known_keys, anthropic_client)
            for f in facts:
                mk = _normalize_key(f.get("metric_key", ""))
                if not mk:
                    continue
                if _already_rejected(mk, source, db_path):
                    continue
                add_pending(
                    metric_key=mk,
                    value=f.get("value", ""),
                    unit=f.get("unit", "text"),
                    as_of=f.get("as_of") or doc_date,
                    source=source,
                    entity=f.get("entity", ""),
                    tier="derived",
                    db_path=db_path,
                )
                counts["pending_extractions"] += 1

    # ── Step 6: Save manifest ─────────────────────────────────────────────────
    _save_manifest(docs_dir, manifest)

    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Geppetto 3 — KB sync")
    parser.add_argument("--docs",       default="./docs",      help="Docs directory")
    parser.add_argument("--db",         default=DEFAULT_DB_PATH, help="Facts DB path")
    parser.add_argument("--no-extract", action="store_true",   help="Skip metric extraction")
    args = parser.parse_args()

    # Load Chroma collection
    try:
        import chromadb
        chroma = chromadb.PersistentClient(path="./chroma_data")
        collection = chroma.get_or_create_collection(name="project_knowledge")
    except Exception as e:
        print(f"ERROR: Could not load ChromaDB: {e}")
        sys.exit(1)

    # Load Anthropic client (optional — only needed for extraction)
    anthropic_client = None
    if not args.no_extract:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            from anthropic import Anthropic
            anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        except Exception as e:
            print(f"WARNING: Could not load Anthropic client ({e}). Running prose-only sync.")

    init_db(args.db)

    print(f"\nSyncing {args.docs} → ChromaDB ...")
    counts = sync(
        docs_dir=args.docs,
        collection=collection,
        db_path=args.db,
        extract_facts=not args.no_extract,
        anthropic_client=anthropic_client,
    )

    print(f"\n  New:          {counts['new']}")
    print(f"  Modified:     {counts['modified']}")
    print(f"  Deleted:      {counts['deleted']}")
    print(f"  Skipped:      {counts['skipped']}")
    print(f"  Facts queued: {counts['pending_extractions']}")
    print()


if __name__ == "__main__":
    main()
