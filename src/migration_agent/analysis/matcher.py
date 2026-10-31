"""Find call sites that match deprecated patterns (2.4).

Owner: Verril (Module 2). Design: Module 2 detailed design, class CallSiteMatcher.
"""

from __future__ import annotations


class CallSiteMatcher:
    def find(self, modules: dict[str, object]) -> list:
        raise NotImplementedError("Module 2 (Verril): CallSiteMatcher")
