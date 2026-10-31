"""Resolve aliases, star imports and re-exports to qualified names (2.3).

Owner: Verril (Module 2). Design: Module 2 detailed design, class SymbolTable.
"""

from __future__ import annotations


class SymbolTable:
    def resolve(self, name: str, module: str) -> str | None:
        raise NotImplementedError("Module 2 (Verril): SymbolTable")
