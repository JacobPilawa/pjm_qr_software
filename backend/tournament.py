from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_ROOT = "https://puzzled.speedpuzzling.nl/api/public/data"
DEFAULT_COMPETITION_ID = "019f7652-3968-7176-b00c-49b7deab1bb4"
LEGACY_EXAMPLE_COMPETITION_ID = "019f2a18-3676-7015-a4cd-c891d7845d77"
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _full_name(member: dict[str, Any]) -> str:
    return " ".join(
        str(member.get(key) or "").strip()
        for key in ("first_name", "insertion", "last_name")
        if str(member.get(key) or "").strip()
    )


def _unwrap(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


@dataclass(frozen=True)
class BackupRound:
    id: str
    name: str
    category: str
    path: Path
    source_url: str
    snapshot_date: str
    assignments: dict[int, dict[str, Any]]

    def metadata(self) -> dict[str, Any]:
        assigned = sum(item.get("status") != "unassigned" for item in self.assignments.values())
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "tables": len(self.assignments),
            "assignments": assigned,
            "sourceUrl": self.source_url,
            "snapshotDate": self.snapshot_date,
        }


def load_backup_round(path: Path) -> BackupRound:
    assignments: dict[int, dict[str, Any]] = {}
    round_id = round_name = category = source_url = snapshot_date = ""
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            round_id = row["round_id"].strip()
            round_name = row["round_name"].strip()
            category = row["category"].strip()
            source_url = row.get("source_url", "").strip()
            snapshot_date = row.get("snapshot_date", "").strip()
            table = int(row["table"])
            member_names = [name.strip() for name in row.get("members", "").split("|") if name.strip()]
            entry_name = row.get("entry_name", "").strip()
            assignments[table] = {
                "ok": bool(entry_name),
                "table": table,
                "roundId": round_id,
                "roundName": round_name,
                "category": category,
                "name": entry_name,
                "teamName": entry_name if category in {"pair", "team"} else "",
                "members": member_names,
                "memberNames": " · ".join(member_names),
                "nationality": row.get("nationality", "").strip(),
                "status": row.get("status", "assigned").strip(),
                "source": "backup",
                "sourceLabel": "PJM schedule PDF backup",
                "sourceUrl": source_url,
                "snapshotDate": snapshot_date,
            }
    if not round_id or not assignments:
        raise ValueError(f"No usable backup assignments in {path.name}")
    return BackupRound(round_id, round_name, category, path, source_url, snapshot_date, assignments)


class EventDataService:
    """Atomic API/PDF roster switcher. API and backups are never merged."""

    def __init__(self, backup_directory: Path, qr_to_table: dict[str, int]) -> None:
        self._lock = threading.RLock()
        self.qr_to_table = dict(qr_to_table)
        self.backups = {
            item.id: item
            for path in sorted(backup_directory.glob("*.csv"))
            for item in [load_backup_round(path)]
        }
        if not self.backups:
            raise RuntimeError("No backup roster CSVs were found")
        first_backup = next(iter(self.backups.values()))
        self.mode = "api"
        self.competition_id = os.environ.get("PJM_COMPETITION_ID", DEFAULT_COMPETITION_ID)
        self.api_rounds: list[dict[str, Any]] = []
        self.api_round_id = ""
        self.api_assignments: dict[int, dict[str, Any]] = {}
        self.backup_round_id = first_backup.id
        self.manual_assignment: dict[str, Any] = {
            "ok": True,
            "table": 1,
            "roundId": "manual",
            "roundName": "Manual Override",
            "category": "manual",
            "name": "Manual Competitor",
            "teamName": "",
            "members": [],
            "memberNames": "",
            "nationality": "",
            "status": "manual",
            "source": "manual",
            "sourceLabel": "Manual override",
            "sourceUrl": "",
        }
        self.status = "loading"
        self.last_error = ""
        self.last_good_at = 0.0
        self.api_last_good_at = 0.0
        self.refresh_seconds = 15

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_metadata_locked()
            return {
                "dataMode": self.mode,
                "competitionId": self.competition_id,
                "competitionIdIsExample": self.competition_id == LEGACY_EXAMPLE_COMPETITION_ID,
                "dataStatus": self.status,
                "dataError": self.last_error,
                "dataLastRefresh": self.api_last_good_at if self.mode == "api" else self.last_good_at,
                "dataRefreshSeconds": self.refresh_seconds,
                "dataRoundId": active.get("id", ""),
                "dataRoundName": active.get("name", "No round selected"),
                "dataCategory": active.get("category", ""),
                "dataAssignmentCount": active.get("assignments", 0),
                "dataTableCount": active.get("tables", 0),
                "dataSourceLabel": {
                    "api": "Live tournament API",
                    "backup": "PJM schedule PDF backup",
                    "manual": "Manual override",
                }[self.mode],
                "apiRounds": [dict(item) for item in self.api_rounds],
                "backupRounds": [item.metadata() for item in self.backups.values()],
            }

    def _active_metadata_locked(self) -> dict[str, Any]:
        if self.mode == "backup":
            return self.backups[self.backup_round_id].metadata()
        if self.mode == "manual":
            return {
                "id": "manual",
                "name": self.manual_assignment["roundName"],
                "category": "manual",
                "tables": 1,
                "assignments": 1,
            }
        selected = next((item for item in self.api_rounds if item["id"] == self.api_round_id), None)
        return {
            **(selected or {}),
            "tables": len(self.api_assignments),
            "assignments": len(self.api_assignments),
        }

    def set_mode(self, mode: str) -> None:
        if mode not in {"api", "backup", "manual"}:
            raise ValueError("Data mode must be api, backup, or manual")
        with self._lock:
            self.mode = mode
            self.last_error = ""
            if mode in {"backup", "manual"}:
                self.status = "ready"
                self.last_good_at = time.time()
            elif self.api_assignments:
                self.status = "ready"
            else:
                self.status = "loading"

    def configure_competition(self, competition_id: str) -> None:
        competition_id = competition_id.strip()
        if not UUID_PATTERN.fullmatch(competition_id):
            raise ValueError("Competition ID must be a UUID such as 019f…")
        with self._lock:
            self.competition_id = competition_id
            self.api_rounds = []
            self.api_round_id = ""
            self.api_assignments = {}
            self.status = "loading"
            self.last_error = ""
        self.refresh_configuration()

    def select_round(self, round_id: str) -> None:
        with self._lock:
            mode = self.mode
            if mode == "backup":
                if round_id not in self.backups:
                    raise ValueError("Backup round was not found")
                self.backup_round_id = round_id
                self.status = "ready"
                self.last_error = ""
                self.last_good_at = time.time()
                return
            if mode == "manual":
                raise ValueError("Manual override does not use tournament rounds")
            if not any(item["id"] == round_id for item in self.api_rounds):
                raise ValueError("API round was not found")
            self.api_round_id = round_id
            self.status = "loading"
        self.refresh_current()

    def refresh_configuration(self) -> None:
        with self._lock:
            competition_id = self.competition_id
            previous_round = self.api_round_id
        try:
            payload = self._request(f"competition/{competition_id}/rounds?language=en-GB")
            raw_rounds = _unwrap(payload)
            if not isinstance(raw_rounds, list) or not raw_rounds:
                raise ValueError("The competition returned no rounds")
            rounds = [
                {
                    "id": str(item["id"]),
                    "name": str(item.get("name") or "Unnamed round"),
                    "category": str(item.get("category") or ""),
                }
                for item in raw_rounds
            ]
            with self._lock:
                if competition_id != self.competition_id:
                    return
                # A slow startup/configuration request must not overwrite a round
                # the operator selected while that request was in flight.
                current_round = self.api_round_id
                default_round = next((item["id"] for item in rounds if "setup" not in item["name"].lower()), rounds[0]["id"])
                selected = (
                    current_round if any(item["id"] == current_round for item in rounds)
                    else previous_round if any(item["id"] == previous_round for item in rounds)
                    else default_round
                )
                self.api_rounds = rounds
                self.api_round_id = selected
            self.refresh_current()
        except Exception as error:
            self._record_error(error)
            raise

    def refresh_current(self) -> None:
        with self._lock:
            round_id = self.api_round_id
            competition_id = self.competition_id
        if not round_id:
            self.refresh_configuration()
            return
        try:
            payload = self._request(f"round/{round_id}/assignments")
            raw_assignments = _unwrap(payload)
            if not isinstance(raw_assignments, list):
                raise ValueError("The assignment response was not a list")
            normalized: dict[int, dict[str, Any]] = {}
            with self._lock:
                round_meta = next((item for item in self.api_rounds if item["id"] == round_id), {})
            for item in raw_assignments:
                value = self._normalize_api_assignment(item, round_meta)
                if value is not None:
                    normalized[value["table"]] = value
            with self._lock:
                if competition_id != self.competition_id or round_id != self.api_round_id:
                    return
                self.api_assignments = normalized
                self.status = "ready"
                self.last_error = ""
                self.api_last_good_at = time.time()
        except Exception as error:
            self._record_error(error)
            raise

    def resolve_qr(self, qr_value: str) -> dict[str, Any]:
        table = self.qr_to_table.get(qr_value)
        if table is None:
            return {"ok": False, "qrValue": qr_value, "message": "Decoded QR is not in the fixed table-card mapping."}
        return self.resolve_table(table, qr_value)

    def resolve_table(self, table: int, qr_value: str = "") -> dict[str, Any]:
        with self._lock:
            if self.mode == "backup":
                assignment = self.backups[self.backup_round_id].assignments.get(table)
                round_name = self.backups[self.backup_round_id].name
            elif self.mode == "manual":
                assignment = self.manual_assignment if table == self.manual_assignment["table"] else None
                round_name = self.manual_assignment["roundName"]
            else:
                assignment = self.api_assignments.get(table)
                round_name = next((item["name"] for item in self.api_rounds if item["id"] == self.api_round_id), "active API round")
            if not assignment or not assignment.get("ok"):
                return {
                    "ok": False,
                    "table": table,
                    "qrValue": qr_value,
                    "message": f"Table {table} is unassigned in {round_name}.",
                }
            return {**assignment, "qrValue": qr_value}

    def configure_manual_override(self, table: int, name: str, detail: str, round_name: str) -> dict[str, Any]:
        name = name.strip()
        detail = detail.strip()
        round_name = round_name.strip() or "Manual Override"
        if table < 1 or table > 10000:
            raise ValueError("Manual table must be between 1 and 10000")
        if not name:
            raise ValueError("Manual headline cannot be empty")
        with self._lock:
            self.mode = "manual"
            self.manual_assignment = {
                "ok": True,
                "table": table,
                "roundId": "manual",
                "roundName": round_name,
                "category": "manual",
                "name": name,
                "teamName": "",
                "members": [detail] if detail else [],
                "memberNames": detail,
                "nationality": "",
                "status": "manual",
                "source": "manual",
                "sourceLabel": "Manual override",
                "sourceUrl": "",
            }
            self.status = "ready"
            self.last_error = ""
            self.last_good_at = time.time()
            return dict(self.manual_assignment)

    def list_assignments(self) -> list[dict[str, Any]]:
        with self._lock:
            if self.mode == "backup":
                values = self.backups[self.backup_round_id].assignments.values()
            elif self.mode == "manual":
                values = [self.manual_assignment]
            else:
                values = self.api_assignments.values()
            return [
                {
                    "table": int(item.get("table") or 0),
                    "name": str(item.get("name") or ""),
                    "detail": str(item.get("memberNames") or ""),
                    "status": str(item.get("status") or "unassigned"),
                    "ok": bool(item.get("ok")),
                }
                for item in sorted(values, key=lambda value: int(value.get("table") or 0))
            ]

    def _normalize_api_assignment(self, item: dict[str, Any], round_meta: dict[str, Any]) -> dict[str, Any] | None:
        identifier = item.get("table_identifier") or (item.get("table") or {}).get("identifier")
        try:
            table = int(identifier)
        except (TypeError, ValueError):
            return None
        team = item.get("team") or {}
        members = [name for member in team.get("members") or [] if (name := _full_name(member))]
        category = str(round_meta.get("category") or team.get("category") or "")
        custom_name = bool(team.get("has_custom_name"))
        team_name = str(team.get("name") or "").strip()
        primary = team_name if custom_name and team_name else (members[0] if category == "solo" and members else team_name or " · ".join(members))
        return {
            "ok": bool(primary),
            "table": table,
            "roundId": str(round_meta.get("id") or item.get("round_id") or ""),
            "roundName": str(round_meta.get("name") or "Tournament round"),
            "category": category,
            "name": primary,
            "teamName": team_name if category in {"pair", "team"} else "",
            "members": members,
            "memberNames": " · ".join(members),
            "nationality": str(team.get("nationality") or ""),
            "status": str(item.get("status") or "assigned"),
            "assignmentId": str(item.get("id") or ""),
            "source": "api",
            "sourceLabel": "Live tournament API",
            "sourceUrl": f"{API_ROOT}/round/{round_meta.get('id') or item.get('round_id')}/assignments",
        }

    def _request(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{API_ROOT}/{path}",
            headers={"Accept": "application/json", "User-Agent": "PJM-QR-Operator/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Tournament API returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Tournament API unavailable: {error.reason}") from error

    def _record_error(self, error: Exception) -> None:
        with self._lock:
            self.last_error = str(error)
            self.status = "stale" if self.api_assignments else "error"
