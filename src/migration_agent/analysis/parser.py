"""Parse files with ast, falling back to a Python 2 capable parser (2.2).

Owner: Verril (Module 2). Design: Module 2 detailed design, class SourceParser.
"""

from __future__ import annotations


class SourceParser:
    def parse(self, path: str) -> object:
        raise NotImplementedError("Module 2 (Verril): SourceParser")
