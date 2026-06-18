"""
GEPPETTO 3: FACT STORE
======================
SQLite-backed versioned fact store. Facts are keyed by metric_key with
as_of dates. Current = newest as_of. History = all versions, newest first.

API:
  init_db(db_path)                              create table if not exists
  add_fact(metric_key, value, unit, as_of, ...) append a new version
  update_fact(...)                               alias for add_fact
  current(metric_key, db_path)                  latest Fact or None
  history(metric_key, db_path)                  list[Fact], newest first
  find_by_value(metric_key, value, unit, ...)   versions matching a value
  list_metrics(db_path)                         all known metric_keys
  list_current_facts(db_path)                   current Fact for every metric

Usage:
  from facts import init_db, add_fact, current, history
  init_db()
  add_fact("qa.tests_passed_pct", value=82, unit="percent", as_of="2026-06-14",
           source="qa_status_report.md", entity="QA")
  f = current("qa.tests_passed_pct")
  print(f.value_display())   # "82%"
"""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, List

DEFAULT_DB_PATH = "./facts.db"
STALENESS_DAYS  = 7     # if newest fact is older than this, flag as stale


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    fact_id:     str
    metric_key:  str
    entity:      str
    value:       str        # always stored as string
    unit:        str        # percent | date | bool | money | text | count
    as_of:       str        # YYYY-MM-DD
    source:      str
    ingested_at: str        # ISO-8601 UTC
    supersedes:   Optional[str] = None
    provisional:  bool = False          # True = auto-extracted, awaiting PM confirmation
    confirmed_at: Optional[str] = None  # ISO-8601 UTC timestamp when PM confirmed

    def value_display(self) -> str:
        """Human-friendly value string."""
        if self.unit == "percent":
            try:
                return f"{float(self.value):.0f}%"
            except (ValueError, TypeError):
                return self.value
        if self.unit == "money":
            try:
                return f"${float(self.value):,.0f}"
            except (ValueError, TypeError):
                return self.value
        if self.unit == "count":
            try:
                return str(int(float(self.value)))
            except (ValueError, TypeError):
                return self.value
        return self.value

    def is_stale(self, window_days: int = STALENESS_DAYS) -> bool:
        """True if this fact hasn't been updated in window_days."""
        try:
            fact_date = date.fromisoformat(self.as_of)
            return (date.today() - fact_date).days > window_days
        except ValueError:
            return False

    def days_old(self) -> int:
        try:
            return (date.today() - date.fromisoformat(self.as_of)).days
        except ValueError:
            return 0


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Create tables and indexes if they don't exist. Safe to call repeatedly."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            fact_id      TEXT PRIMARY KEY,
            metric_key   TEXT NOT NULL,
            entity       TEXT NOT NULL DEFAULT '',
            value        TEXT NOT NULL,
            unit         TEXT NOT NULL DEFAULT 'text',
            as_of        TEXT NOT NULL,
            source       TEXT NOT NULL DEFAULT '',
            ingested_at  TEXT NOT NULL,
            supersedes   TEXT,
            provisional  INTEGER NOT NULL DEFAULT 0,
            confirmed_at TEXT
        )
    """)
    # Migrations: add new columns to existing DBs (safe, idempotent)
    for migration in [
        "ALTER TABLE facts ADD COLUMN provisional INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE facts ADD COLUMN confirmed_at TEXT",
    ]:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_facts (
            pending_id   TEXT PRIMARY KEY,
            metric_key   TEXT NOT NULL,
            value        TEXT NOT NULL,
            unit         TEXT NOT NULL DEFAULT 'text',
            as_of        TEXT NOT NULL,
            source       TEXT NOT NULL DEFAULT '',
            entity       TEXT NOT NULL DEFAULT '',
            tier         TEXT NOT NULL DEFAULT 'derived',
            extracted_at TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_metric ON facts(metric_key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_asof ON facts(metric_key, as_of DESC)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_facts(status)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_fact(row) -> Fact:
    return Fact(
        fact_id=row[0], metric_key=row[1], entity=row[2],
        value=row[3], unit=row[4], as_of=row[5],
        source=row[6], ingested_at=row[7], supersedes=row[8],
        provisional=bool(row[9]) if len(row) > 9 else False,
        confirmed_at=row[10] if len(row) > 10 else None,
    )


def _numeric_match(a: str, b: str, tolerance_abs: float) -> bool:
    """True if two numeric strings are within tolerance_abs of each other."""
    try:
        return abs(float(a) - float(b)) <= tolerance_abs
    except (ValueError, TypeError):
        return False


def _values_match(stored: str, queried: str, unit: str) -> bool:
    """
    True if stored and queried values should be considered equal for validation.
    - percent / count : within ±1 unit (handles STT rounding)
    - money           : within 2% relative
    - date            : exact string match (YYYY-MM-DD or human label)
    - text / bool     : case-insensitive exact match
    """
    if unit in ("percent", "count"):
        return _numeric_match(stored, queried, tolerance_abs=1.0)
    if unit == "money":
        try:
            s, q = float(stored), float(queried)
            denom = max(abs(s), abs(q), 1.0)
            return abs(s - q) / denom <= 0.02
        except (ValueError, TypeError):
            pass
    if unit == "date":
        # normalise common separators
        return stored.strip().replace("/", "-") == queried.strip().replace("/", "-")
    return stored.strip().lower() == queried.strip().lower()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_fact(
    metric_key: str,
    value,
    unit: str = "text",
    as_of: str = None,
    source: str = "",
    entity: str = "",
    provisional: bool = False,
    db_path: str = DEFAULT_DB_PATH,
) -> Fact:
    """
    Append a new version of a fact.
    Does NOT overwrite — each call builds the timeline.
    provisional=True marks auto-extracted facts awaiting PM confirmation.
    Returns the newly created Fact.
    """
    if as_of is None:
        as_of = date.today().isoformat()
    fact_id     = uuid.uuid4().hex
    ingested_at = datetime.now(timezone.utc).isoformat()

    # Link to the previous latest version
    prev = current(metric_key, db_path=db_path)
    supersedes_id = prev.fact_id if prev else None

    conn = sqlite3.connect(db_path)

    # Idempotent: skip if exact duplicate already exists (metric_key, as_of, value, source)
    existing = conn.execute(
        """SELECT * FROM facts WHERE metric_key=? AND as_of=? AND value=? AND source=?
           LIMIT 1""",
        (metric_key, as_of, str(value), source)
    ).fetchone()
    if existing:
        conn.close()
        return _row_to_fact(existing)

    conn.execute(
        "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (fact_id, metric_key, entity, str(value), unit,
         as_of, source, ingested_at, supersedes_id, int(provisional), None)
    )
    conn.commit()
    conn.close()
    return Fact(fact_id, metric_key, entity, str(value), unit,
                as_of, source, ingested_at, supersedes_id, provisional, None)


def update_fact(
    metric_key: str,
    value,
    unit: str = "text",
    as_of: str = None,
    source: str = "manual_update",
    entity: str = "",
    db_path: str = DEFAULT_DB_PATH,
) -> Fact:
    """Convenience alias for add_fact (appends a new version)."""
    return add_fact(metric_key, value, unit=unit, as_of=as_of,
                    source=source, entity=entity, db_path=db_path)


def current(metric_key: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Fact]:
    """Return the latest (max as_of) Fact for metric_key, or None."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """SELECT * FROM facts WHERE metric_key=?
           ORDER BY as_of DESC, ingested_at DESC LIMIT 1""",
        (metric_key,)
    ).fetchone()
    conn.close()
    return _row_to_fact(row) if row else None


def history(metric_key: str, db_path: str = DEFAULT_DB_PATH) -> List[Fact]:
    """Return all versions for metric_key, newest first."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT * FROM facts WHERE metric_key=?
           ORDER BY as_of DESC, ingested_at DESC""",
        (metric_key,)
    ).fetchall()
    conn.close()
    return [_row_to_fact(r) for r in rows]


def list_metrics(db_path: str = DEFAULT_DB_PATH) -> List[str]:
    """Return all distinct metric_keys in the store."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT metric_key FROM facts ORDER BY metric_key"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def list_current_facts(db_path: str = DEFAULT_DB_PATH) -> List[Fact]:
    """Return the current (latest) Fact for every metric in the store."""
    keys = list_metrics(db_path)
    result = []
    for k in keys:
        f = current(k, db_path=db_path)
        if f:
            result.append(f)
    return result


def find_by_value(
    metric_key: str,
    value,
    unit: str = "text",
    db_path: str = DEFAULT_DB_PATH,
) -> List[Fact]:
    """
    Return all historical versions of metric_key whose value matches value.
    Uses _values_match logic (tolerant for numeric units).
    """
    return [
        f for f in history(metric_key, db_path=db_path)
        if _values_match(f.value, str(value), unit)
    ]


# ---------------------------------------------------------------------------
# Metric catalog
# ---------------------------------------------------------------------------
# Canonical metric keys, descriptions, and phrase hints used by the LLM tagger.

METRIC_CATALOG = {
    "qa.tests_passed_pct": {
        "entity": "QA",
        "unit": "percent",
        "description": "QA tests passed percentage",
        "hints": "qa percent, qa is at, tests passed, test pass rate, qa done, qa complete, qa progress, qa coverage, qa score, qa status",
    },
    "release.date": {
        "entity": "Release",
        "unit": "date",
        "description": "Production release / go-live date",
        "hints": "release date, go-live, go live, launch date, shipping date, release scheduled, releasing on, release is set",
    },
    "db.engine": {
        "entity": "Database",
        "unit": "text",
        "description": "Primary database engine (e.g. PostgreSQL, MySQL)",
        "hints": "database, db engine, using postgres, using mysql, using postgresql, database choice",
    },
    "db.migration.deadline": {
        "entity": "Database Migration",
        "unit": "date",
        "description": "Database migration completion deadline",
        "hints": "migration deadline, db migration, database migration, migration on track, migration complete",
    },
    "budget.total": {
        "entity": "Budget",
        "unit": "money",
        "description": "Total project budget",
        "hints": "total budget, budget is, project budget, budget of",
    },
    "budget.spent": {
        "entity": "Budget Spent",
        "unit": "money",
        "description": "Budget spent so far",
        "hints": "budget spent, spent so far, burned, cost to date",
    },
    "auth.status": {
        "entity": "Authentication",
        "unit": "text",
        "description": "Authentication feature implementation status",
        "hints": "auth, authentication, oauth, login, sso, sign-in",
    },
    "api.status": {
        "entity": "API",
        "unit": "text",
        "description": "API implementation status",
        "hints": "api, rest api, api endpoints, backend api, api status",
    },
    "mobile.ios.test_pct": {
        "entity": "Mobile iOS",
        "unit": "percent",
        "description": "iOS test pass percentage",
        "hints": "ios tests, ios coverage, ios testing, ios app tests, ios passing",
    },
    "mobile.android.test_pct": {
        "entity": "Mobile Android",
        "unit": "percent",
        "description": "Android test pass percentage",
        "hints": "android tests, android coverage, android testing, android app tests, android passing",
    },
    "team.engineering.headcount": {
        "entity": "Engineering Team",
        "unit": "count",
        "description": "Number of engineers on the project",
        "hints": "engineering team, number of engineers, engineers on the team, team size, headcount",
    },
}


# ---------------------------------------------------------------------------
# Provisional fact management
# ---------------------------------------------------------------------------

def confirm_fact(fact_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Promote a provisional fact to full trust. Returns True if found."""
    confirmed_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "UPDATE facts SET provisional=0, confirmed_at=? WHERE fact_id=? AND provisional=1",
        (confirmed_at, fact_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def reject_fact(fact_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Delete a provisional fact. Returns True if found."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "DELETE FROM facts WHERE fact_id=? AND provisional=1", (fact_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_provisional(db_path: str = DEFAULT_DB_PATH) -> List[Fact]:
    """Return all provisional facts (awaiting PM confirmation)."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT * FROM facts WHERE provisional=1 ORDER BY metric_key, as_of DESC"
    ).fetchall()
    conn.close()
    return [_row_to_fact(r) for r in rows]


# ---------------------------------------------------------------------------
# Pending queue  (derived-tier extractions — held until PM accepts)
# ---------------------------------------------------------------------------

def add_pending(
    metric_key: str,
    value,
    unit: str = "text",
    as_of: str = None,
    source: str = "",
    entity: str = "",
    tier: str = "derived",
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    """Add an extracted metric to the pending queue. Returns pending_id."""
    if as_of is None:
        as_of = date.today().isoformat()
    pending_id   = uuid.uuid4().hex
    extracted_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO pending_facts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pending_id, metric_key, str(value), unit, as_of,
         source, entity, tier, extracted_at, "pending")
    )
    conn.commit()
    conn.close()
    return pending_id


def list_pending(db_path: str = DEFAULT_DB_PATH, status: str = "pending") -> list:
    """Return pending fact dicts with given status ('pending'|'accepted'|'rejected')."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT pending_id, metric_key, value, unit, as_of, source,
                  entity, tier, extracted_at, status
           FROM pending_facts WHERE status=?
           ORDER BY extracted_at DESC""",
        (status,)
    ).fetchall()
    conn.close()
    cols = ["pending_id", "metric_key", "value", "unit", "as_of",
            "source", "entity", "tier", "extracted_at", "status"]
    return [dict(zip(cols, r)) for r in rows]


def accept_pending(pending_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Fact]:
    """
    Accept a pending extraction: move it to the facts table as a derived fact.
    Returns the created Fact, or None if pending_id not found.
    """
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT * FROM pending_facts WHERE pending_id=? AND status='pending'",
        (pending_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute(
        "UPDATE pending_facts SET status='accepted' WHERE pending_id=?", (pending_id,)
    )
    conn.commit()
    conn.close()
    # Columns: pending_id, metric_key, value, unit, as_of, source, entity, tier, extracted_at, status
    return add_fact(
        metric_key=row[1], value=row[2], unit=row[3],
        as_of=row[4], source=row[5], entity=row[6],
        provisional=False, db_path=db_path
    )


def reject_pending(pending_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Mark a pending extraction as rejected. Returns True if found."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "UPDATE pending_facts SET status='rejected' WHERE pending_id=? AND status='pending'",
        (pending_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0
