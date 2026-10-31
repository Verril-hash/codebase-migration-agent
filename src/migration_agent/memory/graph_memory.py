"""Persistent state of every file, outside the language model prompt.

Owner: Prabanjan (Module 4). Design: SDD section 6.1 (FileNode record).
"""

from __future__ import annotations

from pathlib import Path

from migration_agent.models import AnalysisResult, FileNode, FileState


class GraphMemory:
    @classmethod
    def from_analysis(cls, analysis: AnalysisResult) -> GraphMemory:
        """One FileNode per file, with its dependencies and call sites filled in."""
        raise NotImplementedError("Module 4 (Prabanjan): GraphMemory.from_analysis")

    def get(self, path: str) -> FileNode:
        raise NotImplementedError("Module 4 (Prabanjan): GraphMemory.get")

    def update_state(self, path: str, state: FileState) -> None:
        raise NotImplementedError("Module 4 (Prabanjan): GraphMemory.update_state")

    def save(self, path: Path) -> None:
        """Write the whole graph to disk so a run can resume."""
        raise NotImplementedError("Module 4 (Prabanjan): GraphMemory.save")

    @classmethod
    def load(cls, path: Path) -> GraphMemory:
        raise NotImplementedError("Module 4 (Prabanjan): GraphMemory.load")
