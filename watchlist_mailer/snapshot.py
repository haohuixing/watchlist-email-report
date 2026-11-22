"""Snapshot model: sections, rows, display strings, atomic persistence."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DASHES = {"", "-", "--", "\u2014", "\u2013", "N/A", "n/a", "\u2013\u2013"}


def clean_text(value: str) -> str:
    text = str(value).replace("\u200b", " ")
    text = text.replace("arrow_upward", " ").replace("arrow_downward", " ")
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Section:
    name: str
    headers: list[str]
    rows: list[dict[str, str]]
    note: str | None = None

    def count(self) -> int:
        return len(self.rows)


@dataclass
class Snapshot:
    captured_at: str
    sections: list[Section]
    as_of: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def captured_dt(self) -> datetime:
        try:
            value = datetime.fromisoformat(self.captured_at)
        except (TypeError, ValueError):
            value = datetime.now(ET)
        if value.tzinfo is None:
            value = value.replace(tzinfo=ET)
        return value.astimezone(ET)

    def row_count(self) -> int:
        return sum(section.count() for section in self.sections)

    def unique_symbols(self) -> list[str]:
        seen: dict[str, str] = {}
        for section in self.sections:
            for row in section.rows:
                symbol = symbol_of(row)
                if symbol and symbol.casefold() not in seen:
                    seen[symbol.casefold()] = symbol
        return list(seen.values())

    def directions(self) -> tuple[int, int, int]:
        up = down = flat = 0
        seen: set[str] = set()
        for section in self.sections:
            for index, row in enumerate(section.rows):
                symbol = symbol_of(row).casefold() or f"{section.name}:{index}"
                if symbol in seen:
                    continue
                seen.add(symbol)
                direction = change_direction(row)
                if direction > 0:
                    up += 1
                elif direction < 0:
                    down += 1
                else:
                    flat += 1
        return up, down, flat

    def to_dict(self) -> dict:
        return {
            "captured_at": self.captured_at,
            "as_of": self.as_of,
            "sections": [
                {
                    "name": section.name,
                    "headers": section.headers,
                    "rows": section.rows,
                    "note": section.note,
                }
                for section in self.sections
            ],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        sections = [
            Section(
                name=str(item.get("name", "")),
                headers=[str(header) for header in item.get("headers", [])],
                rows=[
                    {str(key): clean_text(value) for key, value in row.items()}
                    for row in item.get("rows", [])
                ],
                note=item.get("note"),
            )
            for item in data.get("sections", [])
        ]
        return cls(
            captured_at=str(data.get("captured_at", "")),
            sections=sections,
            as_of=data.get("as_of"),
            notes=[str(note) for note in data.get("notes", [])],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> Snapshot:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _find_key(row: dict[str, str], *needles: str, exclude: tuple[str, ...] = ()) -> str | None:
    for key in row:
        lowered = key.casefold()
        if any(needle in lowered for needle in needles) and not any(
            blocked in lowered for blocked in exclude
        ):
            return key
    return None


def symbol_of(row: dict[str, str]) -> str:
    key = _find_key(row, "symbol", "ticker")
    if key is None and row:
        key = next(iter(row))
    if key is None:
        return ""
    return clean_text(row.get(key, ""))


def change_cell(row: dict[str, str]) -> str:
    key = _find_key(row, "% change", "percent", "pct")
    if key is None:
        key = _find_key(row, "change")
    return clean_text(row.get(key, "")) if key else ""


def change_direction(row: dict[str, str]) -> int:
    value = change_cell(row).replace(",", "").strip()
    if not value or value in DASHES:
        return 0
    if "▲" in value or "arrow_upward" in value:
        return 1
    if "▼" in value or "arrow_downward" in value:
        return -1
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    if match:
        number = float(match.group())
        return 1 if number > 0 else -1 if number < 0 else 0
    if value[0] in "-(\u2212":
        return -1
    return 0


def write_watchlist_txt(snapshot: Snapshot, path: Path) -> None:
    lines = [
        f"# Synced: {snapshot.captured_at}",
        "# Source: Google Finance",
        "",
    ]
    for section in snapshot.sections:
        lines.append(f"# List: {section.name}")
        for row in section.rows:
            symbol = symbol_of(row)
            if symbol:
                lines.append(symbol)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
