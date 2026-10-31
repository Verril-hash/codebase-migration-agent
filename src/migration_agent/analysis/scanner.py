"""Collect Python source files and apply exclude rules (sub-component 2.1).

Owner: Verril (Module 2). Design: Module 2 detailed design, class RepositoryScanner.
"""

from __future__ import annotations


class RepositoryScanner:
    def scan(self) -> list[str]:
        raise NotImplementedError("Module 2 (Verril): RepositoryScanner")
