"""Breadth-first walk up the call graph to find impacted callers (2.6).

Owner: Verril (Module 2). Design: Module 2 detailed design, class ImpactAnalyzer.
"""

from __future__ import annotations


class ImpactAnalyzer:
    def compute(self, call_sites: list, call_graph: dict[str, list[str]]) -> object:
        raise NotImplementedError("Module 2 (Verril): ImpactAnalyzer")
