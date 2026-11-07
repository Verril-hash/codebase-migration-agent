"""Structured JSON log of every step the agent takes.

Owner: Prabanjan (Module 8). Design: SDD section 6.3 (LogEntry).

The agent calls :meth:`TrajectoryLog.log_event` once per step and
:meth:`TrajectoryLog.save` once at the end. For long runs, ``live_path`` can
also be supplied to append each event as JSON Lines immediately.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

from migration_agent.models import LogEntry

SCHEMA_VERSION = 1


def to_jsonable(value: Any) -> Any:
    """Convert a value into data that can be serialized as strict JSON."""
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return value.as_posix()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(to_jsonable(key)): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((to_jsonable(item) for item in value), key=str)
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    return str(value)


def _write_text_atomic(path: Path, text: str) -> None:
    """Write text atomically so readers never see a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


class TrajectoryLog:
    """Collect migration events and persist them as JSON."""

    def __init__(self, live_path: Path | None = None) -> None:
        self._entries: list[dict[str, Any]] = []
        self._live_path = Path(live_path) if live_path is not None else None
        if self._live_path is not None:
            _write_text_atomic(self._live_path, "")

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Return a deep copy of the logged entries."""
        return json.loads(json.dumps(self._entries, ensure_ascii=False))

    def log_event(self, entry: LogEntry) -> None:
        """Record one immutable snapshot of a trajectory event."""
        if not isinstance(entry, LogEntry):
            raise TypeError(f"log_event needs a LogEntry, got {type(entry).__name__}")
        record = to_jsonable(entry.to_dict())
        self._entries.append(record)
        if self._live_path is not None:
            self._live_path.parent.mkdir(parents=True, exist_ok=True)
            with self._live_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def totals(self) -> dict[str, int]:
        """Return the number of steps and accumulated token counts."""
        return {
            "steps": len(self._entries),
            "tokens_in": sum(int(item["tokens_in"]) for item in self._entries),
            "tokens_out": sum(int(item["tokens_out"]) for item in self._entries),
        }

    def save(self, path: Path) -> None:
        """Atomically save the complete trajectory as one JSON document."""
        document = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "entry_count": len(self._entries),
            "entries": self._entries,
        }
        _write_text_atomic(
            Path(path),
            json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        )


def read_log(path: Path) -> dict[str, Any]:
    """Load a saved trajectory and reject unsupported schema versions."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a trajectory log of schema version {SCHEMA_VERSION}")
    return document
