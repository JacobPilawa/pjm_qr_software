from __future__ import annotations

import asyncio
import base64
import html
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .ndi import NDIFinder
from .roster import RosterCatalog
from .runtime import QRRuntime


ROOT = Path(__file__).resolve().parents[1]
catalog = RosterCatalog(ROOT / "data/rosters")
ndi_finder = NDIFinder()
runtime = QRRuntime(ROOT, catalog, ndi_finder)


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.start()
    yield
    runtime.stop()
    ndi_finder.close()


app = FastAPI(title="PJM QR Operator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SourceUpdate(BaseModel):
    sourceId: str


class BrowserFrame(BaseModel):
    clientId: str = Field(min_length=1, max_length=100)
    image: str = Field(max_length=20_000_000)


class RosterUpdate(BaseModel):
    rosterId: str


class RosterImport(BaseModel):
    filename: str = Field(max_length=180)
    csvText: str = Field(max_length=2_000_000)


class RoundUpdate(BaseModel):
    roundId: str


class DataModeUpdate(BaseModel):
    mode: str = Field(pattern=r"^(api|backup|manual)$")


class CompetitionUpdate(BaseModel):
    competitionId: str = Field(min_length=36, max_length=36)


class ManualOverrideUpdate(BaseModel):
    table: int = Field(ge=1, le=10000)
    name: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=240)
    roundName: str = Field(default="Manual Override", max_length=120)


class LockUpdate(BaseModel):
    locked: bool


class ManualPreview(BaseModel):
    table: int = Field(ge=1, le=10000)


class FocusUpdate(BaseModel):
    acquireHits: int | None = Field(default=None, ge=1, le=12)
    hitWindowSeconds: float | None = Field(default=None, ge=0.2, le=5)
    switchMissingSeconds: float | None = Field(default=None, ge=0, le=5)
    focusHoldSeconds: float | None = Field(default=None, ge=0.2, le=15)
    switchAreaRatio: float | None = Field(default=None, ge=1, le=4)


class OverlayUpdate(BaseModel):
    position: str | None = Field(default=None, pattern=r"^(left|right)$")
    showCity: bool | None = None
    showUsername: bool | None = None
    x: float | None = Field(default=None, ge=0, le=1920)
    y: float | None = Field(default=None, ge=0, le=1080)
    width: float | None = Field(default=None, ge=260, le=1600)
    height: float | None = Field(default=None, ge=100, le=600)


class OverlayEnabledUpdate(BaseModel):
    enabled: bool


class DiagnosticUpdate(BaseModel):
    showBoxes: bool | None = None


class DecoderModeUpdate(BaseModel):
    mode: str = Field(pattern=r"^(fast|advanced)$")


@app.get("/api/status")
def status():
    return runtime.snapshot()


@app.get("/api/sources")
def sources():
    browser = {"id": "browser", "label": "Browser camera / phone", "kind": "browser", "active": True}
    discovered = [source.__dict__ for source in ndi_finder.sources(wait_ms=250)]
    return {
        "sources": [browser, *discovered],
        "ndiAvailable": ndi_finder.available,
        "ndiError": ndi_finder.error,
    }


@app.post("/api/source")
def select_source(update: SourceUpdate):
    if update.sourceId == "browser":
        runtime.select_source("browser", "Browser camera")
        return runtime.snapshot()
    available = {source.id: source for source in ndi_finder.sources(wait_ms=100)}
    selected = available.get(update.sourceId)
    if selected is None:
        return {"error": "The requested NDI source is no longer available."}
    runtime.select_source(selected.id, selected.label)
    return runtime.snapshot()


@app.post("/api/browser-frame")
def browser_frame(update: BrowserFrame):
    if not runtime.accept_browser_client(update.clientId):
        return {"accepted": False, "reason": "another browser camera is active"}
    if "," not in update.image:
        return {"accepted": False, "error": "Invalid image data"}
    try:
        raw = base64.b64decode(update.image.split(",", 1)[1], validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except (ValueError, cv2.error):
        image = None
    if image is None:
        return {"accepted": False, "error": "Image could not be decoded"}
    return {"accepted": runtime.publish_browser_frame(image)}


@app.get("/api/rosters")
def rosters():
    state = runtime.snapshot()
    return {
        "activeRosterId": state["rosterId"],
        "activeRoundId": state["roundId"],
        "rosters": [
            {
                "id": info.id,
                "label": info.label,
                "rounds": info.rounds,
                "tables": info.tables,
                "assignments": info.assignments,
            }
            for info in catalog.list()
        ],
    }


@app.post("/api/roster")
def select_roster(update: RosterUpdate):
    try:
        runtime.select_roster(update.rosterId)
        return runtime.snapshot()
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/roster/import")
def import_roster(update: RosterImport):
    try:
        roster = catalog.import_csv(update.filename, update.csvText)
        runtime.select_roster(roster.info().id)
        return {"ok": True, "rosterId": roster.info().id}
    except (OSError, ValueError) as error:
        return {"error": str(error)}


@app.post("/api/round")
def select_round(update: RoundUpdate):
    try:
        runtime.select_round(update.roundId)
        return runtime.snapshot()
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/event-data/mode")
def select_data_mode(update: DataModeUpdate):
    try:
        runtime.set_data_mode(update.mode)
        return runtime.snapshot()
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/event-data/competition")
def configure_competition(update: CompetitionUpdate):
    try:
        runtime.configure_competition(update.competitionId)
        return runtime.snapshot()
    except (RuntimeError, ValueError) as error:
        return {"error": str(error)}


@app.post("/api/event-data/refresh")
def refresh_event_data():
    try:
        runtime.refresh_event_data()
        return runtime.snapshot()
    except (RuntimeError, ValueError) as error:
        return {"error": str(error)}


@app.get("/api/event-data/assignments")
def event_assignments():
    return {"assignments": runtime.list_event_assignments()}


@app.post("/api/event-data/manual")
def configure_manual_override(update: ManualOverrideUpdate):
    try:
        runtime.configure_manual_override(update.table, update.name, update.detail, update.roundName)
        return runtime.snapshot()
    except ValueError as error:
        return {"error": str(error)}


@app.post("/api/take")
def take():
    return {"onAir": runtime.take()}


@app.post("/api/clear")
def clear():
    runtime.clear_on_air()
    return {"onAir": None}


@app.post("/api/candidate/clear")
def clear_candidate():
    runtime.clear_candidate()
    return runtime.snapshot()


@app.post("/api/candidate/lock")
def lock_candidate(update: LockUpdate):
    runtime.set_candidate_lock(update.locked)
    return runtime.snapshot()


@app.post("/api/manual-preview")
def manual_preview(update: ManualPreview):
    runtime.manual_preview(update.table)
    return runtime.snapshot()


@app.post("/api/focus")
def focus_settings(update: FocusUpdate):
    runtime.configure_focus(
        acquire_hits=update.acquireHits,
        hit_window_seconds=update.hitWindowSeconds,
        switch_missing_seconds=update.switchMissingSeconds,
        focus_hold_seconds=update.focusHoldSeconds,
        switch_area_ratio=update.switchAreaRatio,
    )
    return runtime.snapshot()


@app.post("/api/overlay-settings")
def overlay_settings(update: OverlayUpdate):
    runtime.configure_overlay(
        update.position,
        update.showCity,
        update.showUsername,
        update.x,
        update.y,
        update.width,
        update.height,
    )
    return runtime.snapshot()


@app.post("/api/overlay-enabled")
def overlay_enabled(update: OverlayEnabledUpdate):
    runtime.set_overlay_enabled(update.enabled)
    return runtime.snapshot()


@app.post("/api/diagnostic-settings")
def diagnostic_settings(update: DiagnosticUpdate):
    runtime.configure_diagnostics(update.showBoxes)
    return runtime.snapshot()


@app.post("/api/decoder-mode")
def decoder_mode(update: DecoderModeUpdate):
    runtime.configure_decoder_mode(update.mode)
    return runtime.snapshot()


@app.get("/api/preview.mjpg")
def preview():
    def frames():
        sequence = -1
        while True:
            sequence, jpeg = runtime.wait_for_jpeg(sequence)
            if jpeg is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/local-info")
def local_info():
    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return {"addresses": sorted(addresses), "uiPort": 5174}


@app.get("/api/overlay.svg")
def overlay_svg():
    state = runtime.snapshot()
    value = state.get("onAir")
    if not value or not value.get("ok"):
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"/>', media_type="image/svg+xml")
    x = float(state["overlayX"])
    y = float(state["overlayY"])
    width = float(state["overlayWidth"])
    height = float(state["overlayHeight"])
    metadata: list[str] = []
    members = str(value.get("memberNames") or "")
    if state["overlayShowCity"] and members and members != value.get("name"):
        metadata.append(members)
    if state["overlayShowUsername"] and value.get("nationality"):
        metadata.append(str(value["nationality"]))
    round_text = f"{value['roundName'].upper()} · TABLE {value['table']}"
    name = str(value["name"])
    padding = max(20.0, min(52.0, width * 0.0525))
    accent = max(8.0, min(20.0, width * 0.01625))
    text_x = padding + accent
    available = max(100.0, width - text_x - padding)

    def fit(text: str, desired: float, minimum: float, maximum: float, factor: float = 0.58) -> float:
        estimated = available / max(1.0, len(text) * factor)
        return max(minimum, min(maximum, desired, estimated))

    compact = height < 150 or width < 480
    round_size = fit(round_text, height * 0.12, 10, 32, 0.62)
    name_size = fit(name, height * (0.34 if compact else 0.27), 16, 90)
    stacked_meta = len(metadata) > 1 and width < 560 and not compact
    meta_lines = metadata if stacked_meta else ([" · ".join(metadata)] if metadata else [])
    longest_meta = max(meta_lines, key=len, default="")
    meta_size = fit(longest_meta, height * (0.10 if stacked_meta else 0.13), 10, 38)
    round_y = height * 0.23
    name_y = height * (0.66 if compact else 0.56)
    if stacked_meta:
        meta_svg = "".join(
            f'<text x="{text_x:.1f}" y="{height * (0.77 + index * 0.13):.1f}" fill="#d9e1ed" font-size="{meta_size:.1f}">{html.escape(line)}</text>'
            for index, line in enumerate(meta_lines[:2])
        )
    else:
        meta_svg = "" if compact or not meta_lines else f'<text x="{text_x:.1f}" y="{height * 0.82:.1f}" fill="#d9e1ed" font-size="{meta_size:.1f}">{html.escape(meta_lines[0])}</text>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
      <g transform="translate({x:.1f} {y:.1f})" font-family="Poppins,Arial,sans-serif">
        <rect width="{width:.1f}" height="{height:.1f}" rx="{min(10.0, height * .03):.1f}" fill="#0e1138" fill-opacity=".96"/>
        <rect width="{accent:.1f}" height="{height:.1f}" fill="#f1b643"/>
        <text x="{text_x:.1f}" y="{round_y:.1f}" fill="#bfc9dc" font-size="{round_size:.1f}" letter-spacing="2">{html.escape(round_text)}</text>
        <text x="{text_x:.1f}" y="{name_y:.1f}" fill="#ffffff" font-size="{name_size:.1f}" font-weight="700">{html.escape(name)}</text>
        {meta_svg}
      </g>
    </svg>'''
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def websocket_state(socket: WebSocket):
    await socket.accept()
    previous = ""
    try:
        while True:
            snapshot = runtime.snapshot()
            serialized = json.dumps(snapshot, sort_keys=True)
            if serialized != previous:
                await socket.send_text(serialized)
                previous = serialized
            await asyncio.sleep(0.08)
    except (WebSocketDisconnect, RuntimeError):
        return
