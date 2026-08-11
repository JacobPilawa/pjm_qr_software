from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .decoder import QRDecoder
from .focus import FocusSelector
from .roster import Roster, RosterCatalog
from .tournament import EventDataService


@dataclass
class RuntimeState:
    status: str = "starting"
    sourceId: str = "browser"
    sourceLabel: str = "Browser camera"
    frameSequence: int = 0
    resultSequence: int = 0
    width: int = 1920
    height: int = 1080
    sourceFps: float = 0.0
    processedFps: float = 0.0
    inferenceMs: float = 0.0
    detections: list[dict[str, Any]] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    onAir: dict[str, Any] | None = None
    focus: dict[str, Any] = field(default_factory=dict)
    decoderPipeline: str = ""
    activeDecoder: str = "Waiting"
    rosterId: str = ""
    rosterLabel: str = ""
    roundId: str = ""
    roundName: str = ""
    overlayPosition: str = "left"
    overlayEnabled: bool = True
    overlayX: float = 30.0
    overlayY: float = 860.0
    overlayWidth: float = 800.0
    overlayHeight: float = 190.0
    overlayShowCity: bool = True
    overlayShowUsername: bool = True
    showDiagnosticBoxes: bool = True
    message: str = "Starting QR runtime…"


class QRRuntime:
    def __init__(self, root: Path, catalog: RosterCatalog, ndi_finder: Any) -> None:
        self.root = root
        self.catalog = catalog
        self.ndi_finder = ndi_finder
        self.decoder = QRDecoder(root / "assets/models/wechat_qrcode")
        infos = catalog.list()
        if not infos:
            raise RuntimeError("No valid roster CSV files were found")
        self.roster: Roster = catalog.load(infos[0].id)
        roster_info = self.roster.info()
        first_round = roster_info.rounds[0]
        self.focus = FocusSelector()
        self.event_data = EventDataService(root / "data/backup", self.roster.table_by_qr)
        self._state = RuntimeState(
            decoderPipeline=self.decoder.description,
            rosterId=roster_info.id,
            rosterLabel=roster_info.label,
            roundId=first_round["id"],
            roundName=first_round["name"],
            focus=self.focus.snapshot(time.monotonic()),
        )
        self._state_lock = threading.Lock()
        self._focus_lock = threading.Lock()
        self._frame_lock = threading.Condition()
        self._latest_frame: np.ndarray | None = None
        self._latest_jpeg: bytes | None = None
        self._jpeg_sequence = -1
        self._preview_encode_lock = threading.Lock()
        self._last_preview_at = 0.0
        self._browser_frame_times: deque[float] = deque()
        self._browser_client_lock = threading.Lock()
        self._browser_client_id: str | None = None
        self._browser_client_seen = 0.0
        self._manual_table: int | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._ndi_loop, name="pjm-qr-ndi", daemon=True),
            threading.Thread(target=self._inference_loop, name="pjm-qr-inference", daemon=True),
            threading.Thread(target=self._event_data_loop, name="pjm-event-data", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._frame_lock:
            self._frame_lock.notify_all()
        for thread in self._threads:
            thread.join(timeout=3)

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            snapshot = asdict(self._state)
        with self._focus_lock:
            snapshot["focus"] = self.focus.snapshot(time.monotonic())
        snapshot.update(self.event_data.snapshot())
        snapshot["roundId"] = snapshot["dataRoundId"]
        snapshot["roundName"] = snapshot["dataRoundName"]
        return snapshot

    def select_source(self, source_id: str, label: str) -> None:
        if source_id != "browser" and not source_id.startswith("ndi:"):
            raise ValueError("Unsupported source")
        with self._state_lock:
            self._state.sourceId = source_id
            self._state.sourceLabel = label
            self._state.status = "starting"
            self._state.sourceFps = 0.0
            self._state.detections = []
            self._state.message = f"Waiting for {label}…"
        self.clear_candidate()

    def publish_browser_frame(self, frame: np.ndarray) -> bool:
        with self._state_lock:
            if self._state.sourceId != "browser":
                return False
        now = time.monotonic()
        self._browser_frame_times.append(now)
        while self._browser_frame_times and now - self._browser_frame_times[0] > 2.0:
            self._browser_frame_times.popleft()
        fps = 0.0
        if len(self._browser_frame_times) > 1:
            fps = (len(self._browser_frame_times) - 1) / max(0.001, self._browser_frame_times[-1] - self._browser_frame_times[0])
        self._publish_frame(frame, fps, "browser", "Browser camera")
        return True

    def accept_browser_client(self, client_id: str) -> bool:
        """Allow one browser camera publisher at a time.

        Restarting the dashboard can leave older tabs open. Without this lease,
        every tab can keep uploading and decoding its camera independently.
        """
        now = time.monotonic()
        with self._browser_client_lock:
            expired = now - self._browser_client_seen > 1.5
            if self._browser_client_id in {None, client_id} or expired:
                self._browser_client_id = client_id
                self._browser_client_seen = now
                return True
            return False

    def select_roster(self, roster_id: str) -> None:
        roster = self.catalog.load(roster_id)
        info = roster.info()
        first_round = info.rounds[0]
        with self._state_lock:
            self.roster = roster
            self._state.rosterId = info.id
            self._state.rosterLabel = info.label
            self._state.roundId = first_round["id"]
            self._state.roundName = first_round["name"]
            self._state.onAir = None
            self._state.message = f"Loaded {info.label}"
        self.clear_candidate()

    def select_round(self, round_id: str) -> None:
        self.event_data.select_round(round_id)
        data = self.event_data.snapshot()
        with self._state_lock:
            self._state.roundId = data["dataRoundId"]
            self._state.roundName = data["dataRoundName"]
            self._state.onAir = None
            self._state.message = f"Active round: {data['dataRoundName']}"
        self.clear_candidate()

    def set_data_mode(self, mode: str) -> None:
        self.event_data.set_mode(mode)
        data = self.event_data.snapshot()
        with self._state_lock:
            self._state.onAir = None
            self._state.message = f"Data source: {data['dataSourceLabel']}"
        self.clear_candidate()

    def configure_competition(self, competition_id: str) -> None:
        self.event_data.configure_competition(competition_id)
        with self._state_lock:
            self._state.onAir = None
            self._state.message = "Tournament API competition loaded"
        self.clear_candidate()

    def refresh_event_data(self) -> None:
        self.event_data.refresh_current()
        self._refresh_active_assignment()

    def configure_manual_override(self, table: int, name: str, detail: str, round_name: str) -> None:
        result = self.event_data.configure_manual_override(table, name, detail, round_name)
        with self._focus_lock:
            self.focus.clear()
        with self._state_lock:
            self._manual_table = table
            self._state.detections = []
            self._state.candidate = result
            self._state.onAir = {**result, "takenAt": time.time()} if self._state.overlayEnabled else None
            self._state.message = f"Manual override: Table {table} · {name}"

    def list_event_assignments(self) -> list[dict[str, Any]]:
        return self.event_data.list_assignments()

    def manual_preview(self, table: int) -> None:
        with self._state_lock:
            result = self.event_data.resolve_table(table)
            self._manual_table = table
            self._state.candidate = result
            if self._state.overlayEnabled and result.get("ok"):
                self._state.onAir = {**result, "takenAt": time.time()}
            self._state.message = f"Manual preview: Table {table}"

    def clear_candidate(self) -> None:
        with self._state_lock:
            self._manual_table = None
            self._state.candidate = None
            self._state.detections = []
            if self._state.overlayEnabled:
                self._state.onAir = None
        with self._focus_lock:
            self.focus.clear()

    def set_candidate_lock(self, locked: bool) -> None:
        with self._focus_lock:
            self.focus.locked = bool(locked)
        with self._state_lock:
            self._state.message = "Candidate locked" if locked else "Candidate unlocked"

    def take(self) -> dict[str, Any] | None:
        with self._state_lock:
            self._state.overlayEnabled = True
            if self._state.candidate and self._state.candidate.get("ok"):
                self._state.onAir = {**self._state.candidate, "takenAt": time.time()}
                self._state.message = f"On air: Table {self._state.candidate['table']}"
            return self._state.onAir

    def clear_on_air(self) -> None:
        with self._state_lock:
            self._state.overlayEnabled = False
            self._state.onAir = None
            self._state.message = "Overlay cleared"

    def set_overlay_enabled(self, enabled: bool) -> None:
        with self._state_lock:
            self._state.overlayEnabled = enabled
            if not enabled:
                self._state.onAir = None
                self._state.message = "Overlay off"
            elif self._state.candidate and self._state.candidate.get("ok"):
                self._state.onAir = {**self._state.candidate, "takenAt": time.time()}
                self._state.message = f"Overlay on: Table {self._state.candidate['table']}"
            else:
                self._state.message = "Overlay on · waiting for a confirmed QR"

    def configure_focus(self, **settings: Any) -> None:
        with self._focus_lock:
            self.focus.configure(**settings)

    def configure_overlay(
        self,
        position: str | None,
        show_city: bool | None,
        show_username: bool | None,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        with self._state_lock:
            if position in {"left", "right"}:
                self._state.overlayPosition = position
                self._state.overlayX = 30.0 if position == "left" else 1090.0
                self._state.overlayY = 860.0
                self._state.overlayWidth = 800.0
                self._state.overlayHeight = 190.0
            next_width = max(260.0, min(1600.0, width if width is not None else self._state.overlayWidth))
            next_height = max(100.0, min(600.0, height if height is not None else self._state.overlayHeight))
            next_x = max(0.0, min(1920.0 - next_width, x if x is not None else self._state.overlayX))
            next_y = max(0.0, min(1080.0 - next_height, y if y is not None else self._state.overlayY))
            self._state.overlayX = next_x
            self._state.overlayY = next_y
            self._state.overlayWidth = next_width
            self._state.overlayHeight = next_height
            self._state.overlayPosition = "left" if next_x + next_width / 2 < 960 else "right"
            if show_city is not None:
                self._state.overlayShowCity = show_city
            if show_username is not None:
                self._state.overlayShowUsername = show_username

    def configure_diagnostics(self, show_boxes: bool | None) -> None:
        if show_boxes is not None:
            with self._state_lock:
                self._state.showDiagnosticBoxes = show_boxes

    def wait_for_jpeg(self, sequence: int) -> tuple[int, bytes | None]:
        with self._frame_lock:
            self._frame_lock.wait_for(lambda: self._jpeg_sequence != sequence or self._stop.is_set(), timeout=1.0)
            return self._jpeg_sequence, self._latest_jpeg

    def _ndi_loop(self) -> None:
        receiver = None
        connected_source = ""
        while not self._stop.is_set():
            with self._state_lock:
                source_id = self._state.sourceId
                source_label = self._state.sourceLabel
            if not source_id.startswith("ndi:"):
                if receiver is not None:
                    receiver.close()
                    receiver = None
                    connected_source = ""
                self._stop.wait(0.1)
                continue
            if not self.ndi_finder.available:
                self._fail(self.ndi_finder.error or "NDI runtime is unavailable")
                self._stop.wait(1.0)
                continue
            try:
                if receiver is None or connected_source != source_label:
                    if receiver is not None:
                        receiver.close()
                    receiver = self.ndi_finder.receiver(source_label)
                    connected_source = source_label
                captured = receiver.capture(timeout_ms=500)
                if captured is not None:
                    frame, fps, _timestamp = captured
                    self._publish_frame(frame, fps, source_id, source_label)
            except Exception as error:
                self._fail(f"NDI receive failed: {error}")
                if receiver is not None:
                    receiver.close()
                    receiver = None
                self._stop.wait(1.0)
        if receiver is not None:
            receiver.close()

    def _publish_frame(self, frame: np.ndarray, fps: float, source_id: str, source_label: str) -> None:
        height, width = frame.shape[:2]
        with self._state_lock:
            if self._state.sourceId != source_id:
                return
            self._state.frameSequence += 1
            self._state.width = width
            self._state.height = height
            self._state.sourceFps = fps
            self._state.sourceLabel = source_label
            sequence = self._state.frameSequence
        with self._frame_lock:
            self._latest_frame = frame
            self._frame_lock.notify_all()
        # Publish the operator preview independently of inference. Previously the
        # MJPEG frame was encoded only after QR decoding, which made a 30 fps NDI
        # or camera feed *look* as slow as the detector. Keep preview work bounded
        # to 20 fps and 1280 px, while preserving the original frame for detection.
        now = time.monotonic()
        if now - self._last_preview_at >= 0.05 and self._preview_encode_lock.acquire(blocking=False):
            try:
                self._last_preview_at = now
                preview = frame
                if width > 1280:
                    preview_scale = 1280.0 / width
                    preview = cv2.resize(
                        frame,
                        (1280, max(1, round(height * preview_scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if encoded:
                    with self._frame_lock:
                        self._latest_jpeg = jpeg.tobytes()
                        self._jpeg_sequence = sequence
                        self._frame_lock.notify_all()
            finally:
                self._preview_encode_lock.release()
        if sequence == 1:
            with self._state_lock:
                self._state.message = "First frame received"

    def _inference_loop(self) -> None:
        last_sequence = -1
        completed: deque[float] = deque()
        misses_since_rescue = 0
        try:
            while not self._stop.is_set():
                with self._frame_lock:
                    self._frame_lock.wait_for(
                        lambda: self._state.frameSequence != last_sequence or self._stop.is_set(),
                        timeout=1.0,
                    )
                    if self._stop.is_set():
                        return
                    frame = None if self._latest_frame is None else self._latest_frame.copy()
                    sequence = self._state.frameSequence
                if frame is None or sequence == last_sequence:
                    continue
                last_sequence = sequence
                started = time.perf_counter()
                # Most scans run at 1280 px for responsive live operation. When
                # those scans miss repeatedly, periodically retry the untouched
                # source frame so very small/distant codes still get full detail.
                source_height, source_width = frame.shape[:2]
                inference_scale = min(1.0, 1280.0 / max(1, source_width))
                if inference_scale < 1.0:
                    inference_frame = cv2.resize(
                        frame,
                        (1280, max(1, round(source_height * inference_scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                else:
                    inference_frame = frame
                observations = self.decoder.decode(inference_frame)
                observations_from_scaled = inference_frame is not frame
                if observations:
                    misses_since_rescue = 0
                else:
                    misses_since_rescue += 1
                    if inference_scale < 1.0 and misses_since_rescue >= 5:
                        observations = self.decoder.decode(frame)
                        observations_from_scaled = False
                        misses_since_rescue = 0

                if inference_scale < 1.0 and observations and observations_from_scaled:
                    inverse_scale = 1.0 / inference_scale
                    for observation in observations:
                        points = np.asarray(observation["corners"], dtype=np.float32) * inverse_scale
                        observation["corners"] = points
                        observation["area"] = abs(float(cv2.contourArea(points)))
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                now = time.monotonic()
                with self._focus_lock:
                    focus_value = self.focus.update(observations, now)
                    focus_snapshot = self.focus.snapshot(now)

                with self._state_lock:
                    candidate = (
                        self.event_data.resolve_table(self._manual_table)
                        if self._manual_table is not None
                        else self.event_data.resolve_qr(focus_value) if focus_value else None
                    )

                frame_area = max(1.0, float(frame.shape[0] * frame.shape[1]))
                serialized = []
                for observation in observations:
                    table = self.event_data.qr_to_table.get(str(observation["value"]))
                    serialized.append({
                        "value": str(observation["value"]),
                        "table": table,
                        "decoder": str(observation["decoder"]),
                        "corners": np.asarray(observation["corners"], dtype=float).reshape(4, 2).tolist(),
                        "area": float(observation["area"]),
                        "areaPercent": float(observation["area"]) / frame_area * 100.0,
                        "hits": int(observation.get("hits", 0)),
                        "focused": bool(observation.get("focused", False)),
                    })
                serialized.sort(key=lambda item: item["area"], reverse=True)

                stamp = time.perf_counter()
                completed.append(stamp)
                while completed and stamp - completed[0] > 3.0:
                    completed.popleft()
                processed_fps = (len(completed) - 1) / max(0.001, completed[-1] - completed[0]) if len(completed) > 1 else 0.0
                active_decoder = serialized[0]["decoder"] if serialized else "Searching"
                if candidate and candidate.get("ok"):
                    message = f"Ready: Table {candidate['table']} · {candidate['name']}"
                elif focus_snapshot["acquiringValue"]:
                    message = f"Acquiring QR ({focus_snapshot['acquiringHits']}/{focus_snapshot['requiredHits']})"
                elif observations:
                    message = "QR decoded but not present in active roster"
                else:
                    message = "Searching for a table QR code"
                with self._state_lock:
                    self._state.status = "running"
                    self._state.resultSequence = sequence
                    self._state.inferenceMs = elapsed_ms
                    self._state.processedFps = processed_fps
                    self._state.detections = serialized
                    self._state.candidate = candidate
                    if self._state.overlayEnabled:
                        if candidate and candidate.get("ok"):
                            previous_taken_at = (
                                self._state.onAir.get("takenAt")
                                if self._state.onAir
                                and self._state.onAir.get("qrValue") == candidate.get("qrValue")
                                and self._state.onAir.get("roundId") == candidate.get("roundId")
                                else time.time()
                            )
                            self._state.onAir = {**candidate, "takenAt": previous_taken_at}
                        else:
                            self._state.onAir = None
                    self._state.focus = focus_snapshot
                    self._state.activeDecoder = active_decoder
                    self._state.message = message
        except Exception as error:
            self._fail(f"QR pipeline failed: {error}")

    def _event_data_loop(self) -> None:
        try:
            self.event_data.refresh_configuration()
            self._refresh_active_assignment()
        except Exception:
            pass
        while not self._stop.wait(self.event_data.refresh_seconds):
            if self.event_data.snapshot()["dataMode"] != "api":
                continue
            try:
                self.event_data.refresh_current()
                self._refresh_active_assignment()
            except Exception:
                # The service retains its last complete API snapshot and marks it stale.
                pass

    def _refresh_active_assignment(self) -> None:
        with self._state_lock:
            table = self._manual_table
            if table is None and self._state.candidate:
                table = self._state.candidate.get("table")
            if table is None:
                return
            current = self.event_data.resolve_table(int(table), str((self._state.candidate or {}).get("qrValue", "")))
            self._state.candidate = current
            if self._state.overlayEnabled:
                self._state.onAir = {**current, "takenAt": time.time()} if current.get("ok") else None

    def _fail(self, message: str) -> None:
        with self._state_lock:
            self._state.status = "error"
            self._state.message = message
