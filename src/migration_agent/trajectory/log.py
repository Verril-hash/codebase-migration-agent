"""Structured JSON log of every step the agent takes.

Owner: Prabanjan (Module 8). Design: SDD section 6.3 (LogEntry).
"""

from __future__ import annotations

from pathlib import Path

from migration_agent.models import LogEntry


class TrajectoryLog:
    def log_event(self, entry: LogEntry) -> None:
        raise NotImplementedError("Module 8 (Prabanjan): log_event")

    def save(self, path: Path) -> None:
        raise NotImplementedError("Module 8 (Prabanjan): save")
