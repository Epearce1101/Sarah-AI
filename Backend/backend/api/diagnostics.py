"""Diagnostics telemetry endpoints for the Sarah ops console."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.diagnostics.telemetry import build_telemetry_snapshot

router = APIRouter()


@router.get("/api/diagnostics/telemetry")
def api_diagnostics_telemetry():
    return build_telemetry_snapshot()


@router.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(build_telemetry_snapshot())
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
