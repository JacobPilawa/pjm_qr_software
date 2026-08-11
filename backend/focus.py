from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class FocusSelector:
    """Choose one intentional QR without reacting to one-frame noise."""

    def __init__(self) -> None:
        self.acquire_hits = 3
        self.hit_window_seconds = 1.0
        self.switch_missing_seconds = 0.65
        self.focus_hold_seconds = 2.0
        self.switch_area_ratio = 1.35
        self.box_hold_seconds = 0.55
        self.focus_value: str | None = None
        self.acquiring_value: str | None = None
        self.locked = False
        self.last_focus_seen = 0.0
        self.last_focus_observation: dict[str, Any] | None = None
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def configure(
        self,
        acquire_hits: int | None = None,
        hit_window_seconds: float | None = None,
        switch_missing_seconds: float | None = None,
        focus_hold_seconds: float | None = None,
        switch_area_ratio: float | None = None,
    ) -> None:
        if acquire_hits is not None:
            self.acquire_hits = max(1, min(12, int(acquire_hits)))
        if hit_window_seconds is not None:
            self.hit_window_seconds = max(0.2, min(5.0, float(hit_window_seconds)))
        if switch_missing_seconds is not None:
            self.switch_missing_seconds = max(0.0, min(5.0, float(switch_missing_seconds)))
        if focus_hold_seconds is not None:
            self.focus_hold_seconds = max(0.2, min(15.0, float(focus_hold_seconds)))
        if switch_area_ratio is not None:
            self.switch_area_ratio = max(1.0, min(4.0, float(switch_area_ratio)))

    def clear(self) -> None:
        self.focus_value = None
        self.acquiring_value = None
        self.last_focus_observation = None
        self.hits.clear()
        self.locked = False

    def update(self, observations: list[dict[str, Any]], now: float) -> str | None:
        visible = {str(item["value"]): item for item in observations}
        for value, timestamps in list(self.hits.items()):
            while timestamps and now - timestamps[0] > self.hit_window_seconds:
                timestamps.popleft()
            if not timestamps and value != self.focus_value:
                del self.hits[value]
        for value in visible:
            self.hits[value].append(now)

        eligible = [
            item for item in observations
            if len(self.hits[str(item["value"])]) >= self.acquire_hits
        ]
        # Area is intentionally the primary rule. If two table cards are in the
        # shot, the camera's larger/closer target wins.
        challenger = max(eligible, key=lambda item: float(item["area"]), default=None)
        self.acquiring_value = None
        if challenger is None and observations:
            self.acquiring_value = str(max(
                observations,
                key=lambda item: (len(self.hits[str(item["value"])]), float(item["area"])),
            )["value"])

        if self.focus_value in visible:
            current = visible[self.focus_value]
            self.last_focus_seen = now
            self.last_focus_observation = current
            if not self.locked and challenger is not None and str(challenger["value"]) != self.focus_value:
                if float(challenger["area"]) >= float(current["area"]) * self.switch_area_ratio:
                    self._select(challenger, now)
        elif self.focus_value is None:
            if challenger is not None:
                self._select(challenger, now)
        elif not self.locked:
            missing_for = now - self.last_focus_seen
            if challenger is not None and missing_for >= self.switch_missing_seconds:
                self._select(challenger, now)
            elif missing_for > self.focus_hold_seconds:
                self.focus_value = None
                self.last_focus_observation = None

        for item in observations:
            value = str(item["value"])
            item["hits"] = len(self.hits[value])
            item["focused"] = value == self.focus_value
        return self.focus_value

    def _select(self, observation: dict[str, Any], now: float) -> None:
        self.focus_value = str(observation["value"])
        self.last_focus_seen = now
        self.last_focus_observation = observation
        self.acquiring_value = None

    def snapshot(self, now: float) -> dict[str, Any]:
        acquiring_hits = len(self.hits.get(self.acquiring_value or "", ()))
        missing_seconds = max(0.0, now - self.last_focus_seen) if self.focus_value else 0.0
        return {
            "focusValue": self.focus_value,
            "acquiringValue": self.acquiring_value,
            "acquiringHits": acquiring_hits,
            "requiredHits": self.acquire_hits,
            "locked": self.locked,
            "missingSeconds": missing_seconds,
            "acquireHits": self.acquire_hits,
            "hitWindowSeconds": self.hit_window_seconds,
            "switchMissingSeconds": self.switch_missing_seconds,
            "focusHoldSeconds": self.focus_hold_seconds,
            "switchAreaRatio": self.switch_area_ratio,
        }
