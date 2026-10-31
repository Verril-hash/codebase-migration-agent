"""The three headline metrics of the problem statement.

Owner: Prabanjan (Module 9). Design: requirement analysis section III-F.
"""

from __future__ import annotations

from migration_agent.models import TestReport


def completeness(before: int, after: int) -> float:
    """Migration completeness: (B - A) / B x 100, from oracle counts."""
    raise NotImplementedError("Module 9 (Prabanjan): completeness")


def test_pass_rate(report: TestReport) -> float:
    raise NotImplementedError("Module 9 (Prabanjan): test_pass_rate")
