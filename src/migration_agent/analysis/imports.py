"""Build the file dependency graph (2.3).

Owner: Verril (Module 2). Design: Module 2 detailed design, class ImportGraphBuilder.
"""

from __future__ import annotations


class ImportGraphBuilder:
    def build(self, modules: dict[str, object]) -> dict[str, list[str]]:
        raise NotImplementedError("Module 2 (Verril): ImportGraphBuilder")
