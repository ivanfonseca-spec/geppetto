"""
PHASE 3 (real-time): WEBSOCKET CONNECTION MANAGER
=================================================
Manages dashboard WebSocket clients per live session and broadcasts live
messages (FR-11). Message schema matches REQUIREMENTS_REALTIME.md §8.3:

    {"type": "alert",   "data": { <claim validation result, §7.1> }}
    {"type": "status",  "data": {"status": "processing", "claim_count": 12}}
    {"type": "ended",   "data": {"folder_name": "2026-06-15_21-05-01"}}
    {"type": "warning", "data": {"message": "Audio unclear — check VB-Cable"}}

Reconnect behavior (NFR-9 / AC-6): each session keeps an in-memory alert
history; when a (re)connecting client attaches, the accumulated alerts are
replayed so a dashboard refresh mid-session doesn't lose anything.

This module has no FastAPI app of its own — phase3_server_realtime.py owns the
WebSocket route and calls into this manager.
"""

import json
from typing import Dict, List, Set

try:
    from starlette.websockets import WebSocketState
except ImportError:  # keeps the module importable without the dep present
    WebSocketState = None


class ConnectionManager:
    def __init__(self, replay_limit: int = 200):
        # session_id -> set of live WebSocket connections
        self.active: Dict[str, Set] = {}
        # session_id -> list of alert payloads (for replay on reconnect)
        self.alert_history: Dict[str, List[dict]] = {}
        self.replay_limit = replay_limit

    # ----- lifecycle --------------------------------------------------------
    async def connect(self, session_id: str, websocket):
        """Accept a client, register it, and replay accumulated alerts."""
        await websocket.accept()
        self.active.setdefault(session_id, set()).add(websocket)
        for alert in self.alert_history.get(session_id, []):
            await self._safe_send(websocket, {"type": "alert", "data": alert})

    def disconnect(self, session_id: str, websocket):
        conns = self.active.get(session_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                self.active.pop(session_id, None)

    def clear_session(self, session_id: str):
        """Drop all state for a session (call after final save)."""
        self.active.pop(session_id, None)
        self.alert_history.pop(session_id, None)

    # ----- sending ----------------------------------------------------------
    async def _safe_send(self, websocket, message: dict) -> bool:
        try:
            if WebSocketState is not None and \
               getattr(websocket, "application_state", None) != WebSocketState.CONNECTED:
                return False
            await websocket.send_text(json.dumps(message))
            return True
        except Exception:
            return False

    async def broadcast(self, session_id: str, message: dict):
        dead = []
        for ws in list(self.active.get(session_id, set())):
            if not await self._safe_send(ws, message):
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)

    async def send_alert(self, session_id: str, claim_result: dict):
        """Record (for replay) and broadcast an alert."""
        hist = self.alert_history.setdefault(session_id, [])
        hist.append(claim_result)
        if len(hist) > self.replay_limit:
            del hist[:-self.replay_limit]
        await self.broadcast(session_id, {"type": "alert", "data": claim_result})

    async def send_status(self, session_id: str, status: str, claim_count: int):
        await self.broadcast(session_id, {
            "type": "status",
            "data": {"status": status, "claim_count": claim_count},
        })

    async def send_ended(self, session_id: str, folder_name: str):
        await self.broadcast(session_id, {
            "type": "ended", "data": {"folder_name": folder_name},
        })

    async def send_warning(self, session_id: str, message: str):
        await self.broadcast(session_id, {
            "type": "warning", "data": {"message": message},
        })

    # ----- introspection ----------------------------------------------------
    def client_count(self, session_id: str) -> int:
        return len(self.active.get(session_id, set()))

    def alert_count(self, session_id: str) -> int:
        return len(self.alert_history.get(session_id, []))
