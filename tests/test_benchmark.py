"""Tests for Module 9 benchmark metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration_agent.benchmark.harness import CorpusError, Hooks, load_tasks, run_suite
from migration_agent.benchmark.metrics import (
    FAILED,
    GREEN,
    PARTIAL,
    aggregate,
    completeness,
    log_usage,
    test_pass_rate,
    verdict,
)
from migration_agent.models import RunConfig, TaskResult, TestReport


def test_completeness_handles_normal_and_empty_oracles() -> None:
    assert completeness(10, 2) == 80.0
    assert completeness(0, 0) == 100.0
    assert completeness(0, 1) == 0.0
    assert completeness(2, 4) == 0.0


def test_completeness_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        completeness(-1, 0)
    with pytest.raises(ValueError):
        completeness(1, -1)


def test_test_pass_rate_excludes_skipped_tests() -> None:
    assert test_pass_rate(TestReport(passed=3, failed=1, errors=1, skipped=20)) == 60.0
    assert test_pass_rate(TestReport(skipped=2)) == 0.0


def test_verdict_classifies_boundary_values() -> None:
    assert verdict(100.0, 100.0) == GREEN
    assert verdict(50.0, 100.0) == PARTIAL
    assert verdict(0.0, 100.0) == FAILED
    assert verdict(100.0, 0.0) == FAILED


def test_log_usage_prefers_final_llm_totals() -> None:
    document = {
        "entries": [
            {"tokens_in": 2, "tokens_out": 3},
            {
                "module": "trajectory",
                "action": "finished",
                "detail": {"llm": {"tokens_in": 10, "tokens_out": 20}},
                "tokens_in": 4,
                "tokens_out": 5,
            },
        ]
    }
    assert log_usage(document) == {"tokens": 30, "steps": 2}


def test_log_usage_falls_back_to_entry_totals() -> None:
    document = {"entries": [{"tokens_in": 2, "tokens_out": 3}, {"tokens_in": 4}]}
    assert log_usage(document) == {"tokens": 9, "steps": 2}


def test_aggregate_reports_empty_and_nonempty_suites() -> None:
    assert aggregate([]) == {
        "tasks": 0,
        "green": 0,
        "partial": 0,
        "failed": 0,
        "green_rate": 0.0,
        "mean_completeness": 0.0,
        "mean_test_pass_rate": 0.0,
        "total_tokens": 0,
        "mean_tokens": 0.0,
        "total_steps": 0,
        "mean_steps": 0.0,
    }
    rows = [
        TaskResult("a", 100.0, 100.0, 10, 2, GREEN),
        TaskResult("b", 50.0, 80.0, 30, 4, PARTIAL),
    ]
    summary = aggregate(rows)
    assert summary["tasks"] == 2
    assert summary["green_rate"] == 50.0
    assert summary["mean_completeness"] == 75.0
    assert summary["total_tokens"] == 40
    assert summary["mean_steps"] == 3.0


def make_task(root: Path, task_id: str = "task_001") -> Path:
    """Create the smallest valid corpus task."""
    task = root / task_id
    (task / "before").mkdir(parents=True)
    (task / "expected").mkdir()
    (task / "before" / "app.py").write_text("old()\n", encoding="utf-8")
    (task / "expected" / "app.py").write_text("new()\n", encoding="utf-8")
    (task / "task.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "spec": "spec.json",
                "expected_call_sites": [{"file": "app.py", "line": 1, "pattern_id": "old"}],
            }
        ),
        encoding="utf-8",
    )
    return task


def test_load_tasks_reports_all_malformed_tasks(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "missing_meta").mkdir()
    (corpus / "wrong_id").mkdir()
    (corpus / "wrong_id" / "task.json").write_text(
        json.dumps({"task_id": "different", "expected_call_sites": {}}), encoding="utf-8"
    )

    with pytest.raises(CorpusError) as error:
        load_tasks(corpus)

    assert "missing_meta: task.json is missing" in str(error.value)
    assert "wrong_id: task_id 'different' must equal the folder name" in str(error.value)
    assert "wrong_id: the before/ folder is missing" in str(error.value)
    assert "wrong_id: expected_call_sites must be a list" in str(error.value)


def test_run_suite_uses_scratch_repo_and_writes_reports(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    make_task(corpus)
    (tmp_path / "spec.json").write_text("{}", encoding="utf-8")
    calls = {"count": 0}

    def counter(_: Path, repo: Path) -> int:
        assert repo != corpus / "task_001" / "before"
        calls["count"] += 1
        return 2 if calls["count"] == 1 else 0

    def runner(config: RunConfig, output_dir: Path) -> bool:
        assert Path(config.repo_path).is_dir()
        assert output_dir.name == "task_001"
        return True

    results = run_suite(
        str(corpus),
        RunConfig(repo_path="unused", spec_path=str(tmp_path / "spec.json")),
        Hooks(runner=runner, counter=counter, test_runner=lambda _: TestReport(passed=1)),
        output_dir=tmp_path / "runs",
    )

    assert results[0].completeness == 100.0
    assert results[0].test_pass_rate == 100.0
    assert results[0].verdict == "green"
    assert (tmp_path / "runs" / "results.json").is_file()
    assert (tmp_path / "runs" / "results.csv").is_file()
