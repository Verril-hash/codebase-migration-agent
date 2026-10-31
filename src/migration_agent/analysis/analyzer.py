"""Facade of Module 2: runs the steps of the analyze() flowchart in order.

Owner: Verril. Design: Module 2 detailed design, figure 1.
"""

from __future__ import annotations

from migration_agent.models import AnalysisResult, MigrationSpec


class StaticAnalyzer:
    def __init__(self, spec: MigrationSpec, repo_path: str) -> None:
        self.spec = spec
        self.repo_path = repo_path

    def analyze(self) -> AnalysisResult:
        """Scan, parse, index, match call sites, build the call graph, compute impact."""
        raise NotImplementedError("Module 2 (Verril): StaticAnalyzer.analyze")
