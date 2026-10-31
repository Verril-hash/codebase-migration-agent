"""Count deprecated usages independently of the matcher (2.7).

Owner: Verril (Module 2). Design: Module 2 detailed design, class OracleDetector.
"""

from __future__ import annotations


class OracleDetector:
    def count(self, repo_path: str) -> int:
        raise NotImplementedError("Module 2 (Verril): OracleDetector")
