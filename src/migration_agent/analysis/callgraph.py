"""Build caller-to-callee edges between functions (2.5).

Owner: Verril (Module 2). Design: Module 2 detailed design, class CallGraphBuilder.
"""

from __future__ import annotations


class CallGraphBuilder:
    def build(self, modules: dict[str, object]) -> dict[str, list[str]]:
        raise NotImplementedError("Module 2 (Verril): CallGraphBuilder")
