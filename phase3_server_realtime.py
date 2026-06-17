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
  uvicorn phase3_server_realtime:app --host 127.0.0.1 --port 8000
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

load_dotenv()

app = FastAPI(title="Meeting Truth Layer — Real-Time")
validator = get_validator()          # holds KB collection + report builder
storage = get_storage()
manager = ConnectionManager()
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
sessions = {}                        # session_id -> LiveSession


# ----------------------------------------------------------------------------
# report building (reuse the batch format so history/storage are unchanged)
# ----------------------------------------------------------------------------
def alerts_to_validations(alerts):
    """Map live alert objects (§7.1) back to the batch validation shape that
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
                    recovery_dir=str(storage.base_dir))
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
        # persistent transcription failure on this chunk — warn, keep session alive
        await manager.send_warning(sid, "Transcription hiccup — continuing")
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
    await manager.send_ended(sid, folder)
    manager.clear_session(sid)
    sessions.pop(sid, None)
    return {"folder_name": folder}


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


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


# ----------------------------------------------------------------------------
# dashboard (static; JS reads ?session= and talks to the API/WS at runtime)
# ----------------------------------------------------------------------------
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Meeting Truth Layer — Live</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f4f5f7;color:#222}
  header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:16px 24px;display:flex;align-items:center;gap:16px}
  header h1{font-size:18px;margin:0;flex:1}
  .status{font-size:13px;opacity:.9}
  button{font:inherit;border:0;border-radius:6px;padding:8px 14px;cursor:pointer}
  .btn-start{background:#28a745;color:#fff}.btn-end{background:#dc3545;color:#fff}
  .wrap{display:flex;height:calc(100vh - 56px)}
  .side{width:280px;background:#fff;border-right:1px solid #e3e6ea;overflow-y:auto;padding:12px}
  .side h2{font-size:13px;text-transform:uppercase;color:#888;margin:8px 4px}
  .mtg{padding:10px;border:1px solid #eee;border-radius:6px;margin:6px 0;cursor:pointer;font-size:13px}
  .mtg:hover{background:#f0f1f4}.mtg .nm{font-weight:600}.mtg .meta{color:#777;font-size:12px;margin-top:4px}
  .mtg .del{float:right;color:#c00;cursor:pointer;font-size:12px}
  .main{flex:1;overflow-y:auto;padding:16px 20px}
  .bar{display:flex;align-items:center;gap:12px;margin-bottom:12px}
  .count{font-size:13px;color:#555}
  .cmd{background:#1e1e1e;color:#ddd;padding:8px 10px;border-radius:6px;font-family:monospace;font-size:12px;margin:8px 0;white-space:pre-wrap}
  .alert{background:#fff;border-radius:8px;padding:12px 14px;margin:8px 0;border-left:5px solid #ccc;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .alert .cat{font-weight:700;font-size:12px;letter-spacing:.04em}
  .alert .claim{font-size:15px;margin:4px 0}
  .alert .src{font-size:12px;color:#555}.alert .sug{font-size:13px;color:#333;margin-top:6px}
  .alert .conf{float:right;font-size:11px;color:#888}
  .VERIFIED{border-left-color:#28a745}.CONTRADICTED{border-left-color:#dc3545}
  .UNVERIFIED{border-left-color:#ffc107}.OUTDATED{border-left-color:#6c757d}
  .NEEDS_CLARIFICATION{border-left-color:#17a2b8}
  .cat.VERIFIED{color:#28a745}.cat.CONTRADICTED{color:#dc3545}.cat.UNVERIFIED{color:#b8860b}
  .cat.OUTDATED{color:#6c757d}.cat.NEEDS_CLARIFICATION{color:#17a2b8}
  .toast{position:fixed;bottom:16px;right:16px;background:#333;color:#fff;padding:10px 14px;border-radius:6px;font-size:13px;opacity:.95}
  .empty{color:#999;text-align:center;margin-top:40px}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.4);display:none;align-items:center;justify-content:center}
  .modal .box{background:#fff;width:80%;max-width:800px;max-height:80vh;overflow:auto;border-radius:8px;padding:20px}
</style>
</head>
<body>
<header>
  <h1>📡 Meeting Truth Layer — Live</h1>
  <span class="status" id="status">idle</span>
  <span class="count" id="count"></span>
  <button class="btn-start" id="startBtn">Start live meeting</button>
  <button class="btn-end" id="endBtn" style="display:none">End meeting</button>
</header>
<div class="wrap">
  <aside class="side">
    <h2>Meeting history</h2>
    <div id="history"></div>
  </aside>
  <main class="main">
    <div id="cmd"></div>
    <div id="alerts"><div class="empty">No live session. Click “Start live meeting”.</div></div>
  </main>
</div>
<div class="modal" id="modal"><div class="box" id="modalBox"></div></div>

<script>
let sid=null, ws=null, reconnectTimer=null;
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);

function wsUrl(id){const p=location.protocol==='https:'?'wss':'ws';return `${p}://${location.host}/ws/session/${id}`;}

function connectWS(id){
  sid=id;
  ws=new WebSocket(wsUrl(id));
  ws.onmessage=e=>handle(JSON.parse(e.data));
  ws.onclose=()=>{ if(sid){ clearTimeout(reconnectTimer); reconnectTimer=setTimeout(()=>connectWS(sid),1500);} };
}

function handle(msg){
  if(msg.type==='alert') addAlert(msg.data);
  else if(msg.type==='status'){ $('status').textContent=msg.data.status; $('count').textContent=msg.data.claim_count+' claims'; }
  else if(msg.type==='warning') toast(msg.data.message);
  else if(msg.type==='ended'){ toast('Saved: '+msg.data.folder_name); endedUI(); loadHistory(); }
}

function addAlert(a){
  const box=$('alerts');
  if(box.querySelector('.empty')) box.innerHTML='';
  const ev=(a.evidence&&a.evidence[0])||{};
  const div=document.createElement('div');
  div.className='alert '+a.category;
  div.innerHTML=`<span class="conf">${a.confidence||''} confidence</span>
    <div class="cat ${a.category}">${a.category.replace('_',' ')}</div>
    <div class="claim">“${esc(a.claim_text)}”</div>
    ${ev.source?`<div class="src">Source: ${esc(ev.source)} — ${esc(ev.snippet||'')}</div>`:''}
    ${a.suggested_response?`<div class="sug">💬 ${esc(a.suggested_response)}</div>`:''}`;
  box.prepend(div);
}

async function start(){
  const r=await fetch('/api/session/start',{method:'POST'});
  const j=await r.json(); sid=j.session_id;
  history.replaceState(null,'','/?session='+sid);
  $('cmd').innerHTML='<div class="cmd">Run the audio streamer:\npy phase1_audio_streaming.py --server '+location.origin+' --session '+sid+'</div>';
  $('alerts').innerHTML='<div class="empty">Listening… alerts will appear here.</div>';
  startedUI(); connectWS(sid);
}
async function end(){ if(!sid)return; await fetch('/api/session/'+sid+'/end',{method:'POST'}); }
function startedUI(){ $('startBtn').style.display='none'; $('endBtn').style.display='inline-block'; $('status').textContent='listening'; }
function endedUI(){ sid=null; if(ws){ws.close();ws=null;} $('endBtn').style.display='none'; $('startBtn').style.display='inline-block'; $('status').textContent='idle'; $('cmd').innerHTML=''; }

async function loadHistory(){
  const r=await fetch('/api/meetings'); const list=await r.json();
  $('history').innerHTML = list.length?'':'<div class="empty" style="margin-top:10px">No saved meetings yet.</div>';
  list.forEach(m=>{
    const d=document.createElement('div'); d.className='mtg';
    d.innerHTML=`<span class="del" data-n="${esc(m.name)}">✕</span>
      <div class="nm">${esc(m.name)}</div>
      <div class="meta">${m.total_claims} claims · 🔴 ${m.contradicted} · ⚠ ${m.critical_issues} critical</div>`;
    d.onclick=ev=>{ if(ev.target.classList.contains('del')){del(m.name);ev.stopPropagation();} else view(m.name); };
    $('history').appendChild(d);
  });
}
async function view(name){
  const r=await fetch('/api/meetings/'+encodeURIComponent(name)); const j=await r.json();
  const s=(j.report&&j.report.summary)||{};
  $('modalBox').innerHTML=`<h2>${esc(name)}</h2>
    <p>${s.total_claims||0} claims · 🟢 ${s.verified||0} · 🔴 ${s.contradicted||0} · 🟡 ${s.unverified||0} · ⏰ ${s.outdated||0} · ❓ ${s.needs_clarification||0}</p>
    <pre style="white-space:pre-wrap;font-size:13px">${esc((j.transcript||'').slice(0,4000))}</pre>
    <button onclick="document.getElementById('modal').style.display='none'">Close</button>`;
  $('modal').style.display='flex';
}
async function del(name){ if(!confirm('Delete '+name+'?'))return; await fetch('/api/meetings/'+encodeURIComponent(name),{method:'DELETE'}); loadHistory(); }

function toast(t){ const e=document.createElement('div'); e.className='toast'; e.textContent=t; document.body.appendChild(e); setTimeout(()=>e.remove(),4000); }
function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

$('startBtn').onclick=start; $('endBtn').onclick=end;
loadHistory();
if(params.get('session')){ sid=params.get('session'); startedUI(); connectWS(sid); }
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    print("Starting Meeting Truth Layer (real-time) on http://127.0.0.1:8000 …")
    uvicorn.run(app, host="127.0.0.1", port=8000)
