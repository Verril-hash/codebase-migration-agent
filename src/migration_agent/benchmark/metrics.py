"""Pure benchmark metrics for migration completeness, tests, and overhead.

Owner: Prabanjan (Module 9). Design: requirement analysis section III-F.

* **Migration completeness**, ``(B - A) / B x 100``: the share of deprecated
  usages that are gone after migration. ``B`` and ``A`` are counts from the
  independent oracle, before and after.
* **Test pass rate**, ``P / T x 100``: the share of the post-migration suite
  that passes. Skipped tests are not part of ``T``.
* **Token and step overhead**: what the run cost, read from the trajectory
  log that Module 8 writes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from migration_agent.models import TaskResult, TestReport

GREEN, PARTIAL, FAILED = "green", "partial", "failed"


def completeness(before: int, after: int) -> float:
    """Return the percentage of oracle-counted deprecated usages removed."""
    if before < 0 or after < 0:
        raise ValueError(f"counts cannot be negative (before={before}, after={after})")
    if before == 0:
        return 100.0 if after == 0 else 0.0
    return max(0.0, 100.0 * (before - after) / before)


def test_pass_rate(report: TestReport) -> float:
    """Return the percentage of executed tests that passed.

    Skipped tests are excluded from the denominator. A report with no
    executed tests scores zero because it provides no passing evidence.
    """
    ran = report.passed + report.failed + report.errors
    if ran == 0:
        return 0.0
    return 100.0 * report.passed / ran


test_pass_rate.__test__ = False


def verdict(completeness_pct: float, pass_rate_pct: float) -> str:
    """Classify a task as green, partial, or failed from its two percentages."""
    if completeness_pct >= 100.0 and pass_rate_pct >= 100.0:
        return GREEN
    if completeness_pct <= 0.0 or pass_rate_pct <= 0.0:
        return FAILED
    return PARTIAL


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def log_usage(document: Mapping[str, Any]) -> dict[str, int]:
    """Return token and step counts from one saved trajectory document."""
    raw_entries = document.get("entries", [])
    if not isinstance(raw_entries, list):
        return {"tokens": 0, "steps": 0}
    entries = [entry for entry in raw_entries if isinstance(entry, Mapping)]
    for entry in reversed(entries):
        if entry.get("action") == "finished":
            detail = entry.get("detail")
            llm = detail.get("llm") if isinstance(detail, Mapping) else None
            if isinstance(llm, Mapping):
                tokens = _as_int(llm.get("tokens_in")) + _as_int(llm.get("tokens_out"))
                return {"tokens": tokens, "steps": len(entries)}
            break
    tokens = sum(
        _as_int(entry.get("tokens_in")) + _as_int(entry.get("tokens_out")) for entry in entries
    )
    return {"tokens": tokens, "steps": len(entries)}


def aggregate(results: Iterable[TaskResult]) -> dict[str, float | int]:
    """Return suite-level counts, rates, means, and overhead totals."""
    rows = list(results)
    count = len(rows)
    verdicts = Counter(row.verdict for row in rows)

    def mean(values: list[float]) -> float:
        return sum(values) / count if count else 0.0

    return {
        "tasks": count,
        "green": verdicts[GREEN],
        "partial": verdicts[PARTIAL],
        "failed": verdicts[FAILED],
        "green_rate": 100.0 * verdicts[GREEN] / count if count else 0.0,
        "mean_completeness": mean([row.completeness for row in rows]),
        "mean_test_pass_rate": mean([row.test_pass_rate for row in rows]),
        "total_tokens": sum(row.tokens for row in rows),
        "mean_tokens": mean([row.tokens for row in rows]),
        "total_steps": sum(row.steps for row in rows),
        "mean_steps": mean([row.steps for row in rows]),
    }
