"""Facade of Module 2: runs the steps of the analyze() flowchart in order.

Owner: Verril. Design: Module 2 detailed design, figure 1. The rest of the
system calls only this class.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from migration_agent.analysis.callgraph import CallGraphBuilder
from migration_agent.analysis.impact import ImpactAnalyzer
from migration_agent.analysis.imports import ImportGraphBuilder
from migration_agent.analysis.matcher import CallSiteMatcher
from migration_agent.analysis.oracle import OracleDetector
from migration_agent.analysis.parser import SourceParser
from migration_agent.analysis.py2 import AVAILABLE as PY2_AVAILABLE
from migration_agent.analysis.py2 import python2_fallback
from migration_agent.analysis.scanner import RepositoryScanner
from migration_agent.analysis.symbols import SymbolTable
from migration_agent.models import AnalysisResult, MigrationSpec


class StaticAnalyzer:
    def __init__(
        self,
        spec: MigrationSpec,
        repo_path: str | Path,
        excludes: Iterable[str] = (),
        impact_depth_limit: int = 5,
    ) -> None:
        self.spec = spec
        self.repo_path = Path(repo_path)
        self.excludes = tuple(excludes)
        self.impact_depth_limit = impact_depth_limit

    def analyze(self) -> AnalysisResult:
        """Scan, parse, index, match call sites, build the call graph, compute impact.

        Raises ``ValueError`` when no file in the repository could be parsed.
        """
        paths = RepositoryScanner(self.repo_path, self.excludes).scan()
        fallback = python2_fallback if PY2_AVAILABLE else None
        modules, failures = SourceParser(self.repo_path, fallback).parse_all(paths)
        if not modules:
            raise ValueError(f"no parsable source files in {self.repo_path}")

        import_graph = ImportGraphBuilder().build(modules)
        symbols = SymbolTable.build(modules)
        matcher = CallSiteMatcher(self.spec.deprecated_patterns, symbols)
        call_sites = matcher.find(modules)
        graph_builder = CallGraphBuilder(symbols)
        call_graph = graph_builder.build(modules)
        impact = ImpactAnalyzer(self.impact_depth_limit).compute(
            call_sites, call_graph, graph_builder.function_files
        )
        return AnalysisResult(
            import_graph=import_graph,
            call_graph=call_graph,
            call_sites=call_sites,
            impact_set=impact,
            unresolved_sites=matcher.unresolved(),
            parse_failures=failures,
        )

    def baseline_count(self, regexes: dict[str, str] | None = None) -> int:
        """Deprecated-usage count from the independent oracle, for the completeness metric."""
        return OracleDetector(self.spec.deprecated_patterns, regexes, self.excludes).count(
            self.repo_path
        )
