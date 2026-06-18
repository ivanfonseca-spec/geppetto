"""
PHASE 3 (real-time): SERVER + LIVE DASHBOARD
============================================
Ties the real-time pieces together:
  - session lifecycle + chunk-ingest endpoints (the audio streamer posts here)
  - WebSocket channel that pushes alerts/status to the dashboard (phase3_websocket)
  - per-session transcription + claim detection + validation (phase3_session)
  - the EXISTING history side panel + saved-report format (phase3_storage), unchanged
  - on session end: build the same batch-style report and save it to meetings/

Run:
  pip install fastapi "uvicorn[standard]" openai anthropic chromadb python-dotenv
  py -m uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
Then open http://127.0.0.1:8000  (and run phase1_audio_streaming.py to feed audio).
"""

import os
import shutil

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from openai import OpenAI

from phase3_session import LiveSession
from phase3_websocket import ConnectionManager
from phase3_integration import get_validator
from phase3_storage import get_storage
from anthropic import Anthropic
from facts import (
    init_db, update_fact, list_current_facts, history as fact_history,
    list_provisional, confirm_fact, reject_fact,
    list_pending, accept_pending, reject_pending,
)
from fact_extractor import seed_known_facts
from kb_sync import sync as kb_sync
from meeting_notes import generate_meeting_notes

load_dotenv()

app = FastAPI(title="Meeting Truth Layer - Real-Time")
validator = get_validator()          # holds KB collection + report builder
storage = get_storage()
manager = ConnectionManager()
openai_client    = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
sessions = {}                        # session_id -> LiveSession

# Paths
FACTS_DB  = "./facts.db"
DOCS_DIR  = "./docs"
_HERE     = os.path.dirname(os.path.abspath(__file__))

# Startup: init fact store, seed baseline facts, then run incremental KB sync
init_db(FACTS_DB)
_seeded = seed_known_facts(db_path=FACTS_DB)
if _seeded:
    print(f"[facts] Seeded {_seeded} facts into {FACTS_DB}")

print("[sync] Running incremental KB sync ...")
_sync_summary = kb_sync(
    docs_dir=os.path.join(_HERE, DOCS_DIR),
    collection=validator.kb_collection,
    db_path=FACTS_DB,
    extract_facts=True,
    anthropic_client=anthropic_client,
)
print(
    f"[sync] Done — new={_sync_summary['new']} modified={_sync_summary['modified']} "
    f"deleted={_sync_summary['deleted']} skipped={_sync_summary['skipped']} "
    f"queued={_sync_summary['pending_extractions']}"
)

# Dashboard HTML loaded from separate file (avoids Python string delimiter issues)
with open(os.path.join(_HERE, "dashboard.html"), encoding="utf-8") as _f:
    DASHBOARD_HTML = _f.read()


# ----------------------------------------------------------------------------
# report building (reuse the batch format so history/storage are unchanged)
# ----------------------------------------------------------------------------
def alerts_to_validations(alerts):
    """Map live alert objects (section 7.1) back to the batch validation shape that
    MeetingValidator._generate_report and storage expect."""
    out = []
    for a in alerts:
        out.append({
            "claim": a.get("claim_text", ""),
            "category": a.get("category", "UNVERIFIED"),
            "confidence": a.get("confidence_score", 0.5),
            "reasoning": a.get("reasoning", ""),
            "pm_action_suggested": a.get("suggested_response", ""),
            "priority": a.get("priority", "LOW"),
            "supporting_sources": [e.get("source") for e in a.get("evidence", [])],
            "conflicting_sources": [],
        })
    return out


def build_and_save(session):
    validations = alerts_to_validations(session.alerts)
    report = validator._generate_report(validations, session.rolling_transcript)
    result = storage.save_meeting(session.rolling_transcript, report)
    return os.path.basename(result["folder"])


# ----------------------------------------------------------------------------
# session lifecycle + ingest
# ----------------------------------------------------------------------------
@app.post("/api/session/start")
async def session_start():
    s = LiveSession(kb_collection=validator.kb_collection, openai_client=openai_client,
                    recovery_dir=str(storage.base_dir), db_path=FACTS_DB)
    sessions[s.id] = s
    return {"session_id": s.id}


@app.post("/api/session/{sid}/chunk")
async def session_chunk(sid: str, request: Request):
    session = sessions.get(sid)
    if not session:
        return JSONResponse(status_code=404, content={"error": "unknown session"})
    body = await request.body()
    await manager.send_status(sid, "processing", session.claim_count)
    try:
        alerts = await run_in_threadpool(session.ingest_chunk, body)
    except Exception:
        # persistent transcription failure on this chunk -- warn, keep session alive
        await manager.send_warning(sid, "Transcription hiccup - continuing")
        await manager.send_status(sid, "listening", session.claim_count)
        return JSONResponse(status_code=202, content={"status": "skipped"})
    for a in alerts:
        await manager.send_alert(sid, a)
    await manager.send_status(sid, "listening", session.claim_count)
    return JSONResponse(status_code=202, content={"alerts": len(alerts)})


@app.post("/api/session/{sid}/end")
async def session_end(sid: str):
    session = sessions.get(sid)
    if not session:
        return JSONResponse(status_code=404, content={"error": "unknown session"})
    await run_in_threadpool(session.finalize)
    folder = await run_in_threadpool(build_and_save, session)

    # Generate end-of-meeting notes → docs/notes/ + meetings/<folder>/
    meeting_folder = str(storage.base_dir / folder)
    notes_result = await run_in_threadpool(
        generate_meeting_notes,
        session.rolling_transcript,
        session.alerts,
        os.path.join(_HERE, DOCS_DIR),
        meeting_folder,
        anthropic_client,
    )
    if notes_result.get("filename"):
        print(f"[notes] Saved {notes_result['filename']}")

    await manager.send_ended(sid, folder)
    manager.clear_session(sid)
    sessions.pop(sid, None)
    return {"folder_name": folder, "notes": notes_result.get("filename")}


@app.websocket("/ws/session/{sid}")
async def session_ws(websocket: WebSocket, sid: str):
    await manager.connect(sid, websocket)   # replays accumulated alerts
    try:
        while True:
            await websocket.receive_text()   # ignore client messages; keepalive
    except WebSocketDisconnect:
        manager.disconnect(sid, websocket)


# ----------------------------------------------------------------------------
# retained history endpoints (unchanged behavior)
# ----------------------------------------------------------------------------
@app.get("/api/meetings")
async def list_meetings():
    return storage.list_meetings()


@app.get("/api/meetings/{folder}")
async def load_meeting(folder: str):
    try:
        return storage.load_meeting(folder)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "not found"})


@app.delete("/api/meetings/{folder}")
async def delete_meeting(folder: str):
    target = storage.base_dir / folder
    if not str(target.resolve()).startswith(str(storage.base_dir.resolve())):
        return JSONResponse(status_code=400, content={"error": "bad path"})
    if target.is_dir():
        shutil.rmtree(target)
        return {"deleted": folder}
    return JSONResponse(status_code=404, content={"error": "not found"})


@app.post("/api/validate")
async def validate_batch(request: Request):
    """Batch fallback: validate a full pasted transcript (kept from MVP)."""
    data = await request.json()
    transcript = data.get("transcript", "")
    report = await run_in_threadpool(validator.validate_meeting, transcript)
    return report


@app.get("/api/health")
async def health():
    return {"status": "ok", "active_sessions": len(sessions)}


# ----------------------------------------------------------------------------
# Geppetto 3: fact store endpoints
# ----------------------------------------------------------------------------

@app.get("/api/metrics")
async def get_metrics():
    """Return the current value for every metric in the fact store."""
    facts = list_current_facts(db_path=FACTS_DB)
    return [
        {
            "metric_key":    f.metric_key,
            "entity":        f.entity,
            "value":         f.value,
            "value_display": f.value_display(),
            "unit":          f.unit,
            "as_of":         f.as_of,
            "source":        f.source,
            "is_stale":      f.is_stale(),
            "days_old":      f.days_old(),
        }
        for f in facts
    ]


@app.get("/api/metrics/{metric_key}/history")
async def get_metric_history(metric_key: str):
    """Return the full version history for one metric."""
    versions = fact_history(metric_key, db_path=FACTS_DB)
    if not versions:
        return JSONResponse(status_code=404, content={"error": "metric not found"})
    return [
        {
            "fact_id":       f.fact_id,
            "metric_key":    f.metric_key,
            "value":         f.value,
            "value_display": f.value_display(),
            "unit":          f.unit,
            "as_of":         f.as_of,
            "source":        f.source,
            "ingested_at":   f.ingested_at,
        }
        for f in versions
    ]


@app.post("/api/metrics/update")
async def api_update_fact(request: Request):
    """
    Update (append a new version of) a metric.
    Body: { metric_key, value, unit?, as_of?, source? }
    """
    data = await request.json()
    metric_key = data.get("metric_key", "").strip()
    value      = data.get("value", "")
    unit       = data.get("unit", "text")
    as_of      = data.get("as_of") or None
    source     = data.get("source", "dashboard_manual_update")
    entity     = data.get("entity", "")

    if not metric_key or value == "":
        return JSONResponse(status_code=400, content={"error": "metric_key and value required"})

    fact = update_fact(
        metric_key, value,
        unit=unit, as_of=as_of, source=source, entity=entity,
        db_path=FACTS_DB
    )
    return {
        "status":        "updated",
        "fact_id":       fact.fact_id,
        "metric_key":    fact.metric_key,
        "value":         fact.value,
        "value_display": fact.value_display(),
        "as_of":         fact.as_of,
    }


# ----------------------------------------------------------------------------
# Geppetto 3: manual sync endpoint
# ----------------------------------------------------------------------------

@app.post("/api/sync")
async def manual_sync():
    """Trigger an incremental KB sync manually (e.g. after adding new docs)."""
    summary = await run_in_threadpool(
        kb_sync,
        os.path.join(_HERE, DOCS_DIR),
        validator.kb_collection,
        FACTS_DB,
        True,
        anthropic_client,
    )
    return summary


# ----------------------------------------------------------------------------
# Geppetto 3: provisional + pending queue endpoints
# ----------------------------------------------------------------------------

@app.get("/api/pending")
async def get_pending():
    """Return all pending (unreviewed) extractions and provisional facts."""
    pending      = list_pending(db_path=FACTS_DB, status="pending")
    provisional  = [
        {
            "fact_id":       f.fact_id,
            "metric_key":    f.metric_key,
            "value":         f.value,
            "value_display": f.value_display(),
            "unit":          f.unit,
            "as_of":         f.as_of,
            "source":        f.source,
            "entity":        f.entity,
            "type":          "provisional",
        }
        for f in list_provisional(db_path=FACTS_DB)
    ]
    for p in pending:
        p["type"] = "pending"
    return {"provisional": provisional, "pending": pending,
            "total": len(provisional) + len(pending)}


@app.post("/api/pending/fact/{fact_id}/confirm")
async def confirm_provisional(fact_id: str):
    ok = confirm_fact(fact_id, db_path=FACTS_DB)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "not found or already confirmed"})
    return {"status": "confirmed", "fact_id": fact_id}


@app.post("/api/pending/fact/{fact_id}/reject")
async def reject_provisional(fact_id: str):
    ok = reject_fact(fact_id, db_path=FACTS_DB)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "not found or not provisional"})
    return {"status": "rejected", "fact_id": fact_id}


@app.post("/api/pending/{pending_id}/accept")
async def accept_pending_fact(pending_id: str):
    fact = accept_pending(pending_id, db_path=FACTS_DB)
    if fact is None:
        return JSONResponse(status_code=404, content={"error": "not found or already resolved"})
    return {"status": "accepted", "fact_id": fact.fact_id,
            "metric_key": fact.metric_key, "value_display": fact.value_display()}


@app.post("/api/pending/{pending_id}/reject")
async def reject_pending_fact(pending_id: str):
    ok = reject_pending(pending_id, db_path=FACTS_DB)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "not found or already resolved"})
    return {"status": "rejected", "pending_id": pending_id}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


if __name__ == "__main__":
    import uvicorn
    print("Starting Meeting Truth Layer (real-time) on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
