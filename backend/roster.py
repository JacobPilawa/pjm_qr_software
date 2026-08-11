from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = {
    "round_id", "round_name", "table", "qr_value",
    "competitor_name", "city", "username",
}


@dataclass(frozen=True)
class RosterInfo:
    id: str
    label: str
    path: Path
    rounds: list[dict[str, str]]
    tables: int
    assignments: int


class Roster:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rounds: dict[str, str] = {}
        self.table_by_qr: dict[str, int] = {}
        self.qr_by_table: dict[int, str] = {}
        self.people: dict[tuple[str, int], dict[str, Any]] = {}
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
            for row_number, row in enumerate(reader, start=2):
                round_id = row["round_id"].strip()
                round_name = row["round_name"].strip()
                qr_value = row["qr_value"].strip()
                try:
                    table = int(row["table"])
                except ValueError as error:
                    raise ValueError(f"Row {row_number}: table must be a number") from error
                if not round_id or not round_name or not qr_value:
                    raise ValueError(f"Row {row_number}: round and QR values cannot be empty")
                if qr_value in self.table_by_qr and self.table_by_qr[qr_value] != table:
                    raise ValueError(f"Row {row_number}: one QR value maps to multiple tables")
                if table in self.qr_by_table and self.qr_by_table[table] != qr_value:
                    raise ValueError(f"Row {row_number}: one table maps to multiple QR values")
                key = (round_id, table)
                if key in self.people:
                    raise ValueError(f"Row {row_number}: duplicate round/table assignment")
                self.rounds.setdefault(round_id, round_name)
                self.table_by_qr[qr_value] = table
                self.qr_by_table[table] = qr_value
                self.people[key] = {
                    "name": row["competitor_name"].strip(),
                    "city": row["city"].strip(),
                    "username": row["username"].strip(),
                }
        if not self.rounds or not self.table_by_qr:
            raise ValueError("CSV contains no usable roster assignments")

    def info(self) -> RosterInfo:
        return RosterInfo(
            id=self.path.stem,
            label=self.path.stem.replace("_", " ").replace("-", " ").title(),
            path=self.path,
            rounds=[{"id": key, "name": value} for key, value in self.rounds.items()],
            tables=len(self.table_by_qr),
            assignments=len(self.people),
        )

    def resolve_qr(self, qr_value: str, round_id: str) -> dict[str, Any]:
        table = self.table_by_qr.get(qr_value)
        if table is None:
            return {"ok": False, "qrValue": qr_value, "message": "Decoded QR is not present in the active CSV."}
        return self.resolve_table(table, round_id, qr_value)

    def resolve_table(self, table: int, round_id: str, qr_value: str | None = None) -> dict[str, Any]:
        person = self.people.get((round_id, table))
        if person is None:
            return {
                "ok": False,
                "table": table,
                "qrValue": qr_value or self.qr_by_table.get(table, ""),
                "message": f"Table {table} has no assignment in the active round.",
            }
        return {
            "ok": True,
            "table": table,
            "qrValue": qr_value or self.qr_by_table.get(table, ""),
            "roundId": round_id,
            "roundName": self.rounds[round_id],
            **person,
        }


class RosterCatalog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[RosterInfo]:
        output: list[RosterInfo] = []
        for path in sorted(self.directory.glob("*.csv")):
            try:
                output.append(Roster(path).info())
            except (OSError, ValueError):
                continue
        return output

    def load(self, roster_id: str) -> Roster:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", roster_id):
            raise ValueError("Invalid roster ID")
        path = self.directory / f"{roster_id}.csv"
        if not path.exists():
            raise ValueError("Roster CSV was not found")
        return Roster(path)

    def import_csv(self, filename: str, text: str) -> Roster:
        if len(text.encode("utf-8")) > 2_000_000:
            raise ValueError("CSV is larger than 2 MB")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem).strip("_") or "imported_roster"
        # Validate from memory before any file is written.
        reader = csv.DictReader(io.StringIO(text))
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
        path = self.directory / f"{slug}.csv"
        path.write_text(text, encoding="utf-8")
        try:
            return Roster(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
