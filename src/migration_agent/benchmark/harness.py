"""Run migration agents over the benchmark corpus and collect metrics.

Owner: Prabanjan (Module 9). The harness validates the corpus before running,
uses scratch repositories for every task, and keeps scoring independent from
the agent's own verification.
"""

from __future__ import annotations

import csv
import dataclasses
import inspect
import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_agent.benchmark.metrics import (
    FAILED,
    aggregate,
    completeness,
    log_usage,
    test_pass_rate,
    verdict,
)
from migration_agent.models import RunConfig, TaskResult, TestReport

_log = logging.getLogger(__name__)
SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("runs") / "benchmark"

Runner = Callable[[RunConfig, Path], Any]
TestRunner = Callable[[str], TestReport]
Counter = Callable[[Path, Path], int]


class CorpusError(ValueError):
    """The corpus is invalid; ``problems`` contains every discovered issue."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s) in the corpus:\n- " + "\n- ".join(problems))


@dataclass(frozen=True)
class Task:
    """Validated metadata and paths for one benchmark task."""

    task_id: str
    path: Path
    before: Path
    expected: Path
    spec_path: Path
    description: str = ""
    author: str = ""
    expected_call_sites: list[dict[str, Any]] = field(default_factory=list)


def _resolve_spec(reference: str, root: Path) -> Path | None:
    given = Path(reference)
    candidates = [given] if given.is_absolute() else [root.resolve().parent / given, given]
    return next((path for path in candidates if path.is_file()), None)


def _site_problems(label: str, sites: Any) -> list[str]:
    if not isinstance(sites, list):
        return [f"{label}: expected_call_sites must be a list"]
    problems = []
    for index, site in enumerate(sites):
        if not (
            isinstance(site, dict)
            and isinstance(site.get("file"), str)
            and isinstance(site.get("line"), int)
            and not isinstance(site.get("line"), bool)
            and isinstance(site.get("pattern_id"), str)
        ):
            problems.append(
                f"{label}: expected_call_sites[{index}] needs a file, a line and a pattern_id"
            )
    return problems


def _load_task(folder: Path, root: Path, default_spec: str | None) -> tuple[Task | None, list[str]]:
    label = folder.name
    meta_path = folder / "task.json"
    if not meta_path.is_file():
        return None, [f"{label}: task.json is missing"]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"{label}: task.json is not valid JSON ({exc})"]
    if not isinstance(meta, dict):
        return None, [f"{label}: task.json must hold a JSON object"]

    problems: list[str] = []
    if meta.get("task_id") != label:
        problems.append(f"{label}: task_id {meta.get('task_id')!r} must equal the folder name")
    for part in ("before", "expected"):
        if not (folder / part).is_dir():
            problems.append(f"{label}: the {part}/ folder is missing")
    sites = meta.get("expected_call_sites", [])
    problems.extend(_site_problems(label, sites))

    reference = meta.get("spec") or default_spec
    spec_path = _resolve_spec(reference, root) if isinstance(reference, str) else None
    if spec_path is None:
        problems.append(f"{label}: spec file {reference!r} was not found")
    if problems or spec_path is None:
        return None, problems

    return (
        Task(
            task_id=label,
            path=folder,
            before=folder / "before",
            expected=folder / "expected",
            spec_path=spec_path,
            description=str(meta.get("description", "")),
            author=str(meta.get("author", "")),
            expected_call_sites=sites,
        ),
        [],
    )


def load_tasks(tasks_dir: str | Path, default_spec: str | Path | None = None) -> list[Task]:
    """Read and validate all task folders, sorted by task id."""
    root = Path(tasks_dir)
    if not root.is_dir():
        raise CorpusError([f"tasks directory does not exist: {root}"])
    folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
    tasks: list[Task] = []
    problems: list[str] = []
    for folder in folders:
        task, found = _load_task(folder, root, str(default_spec) if default_spec else None)
        problems.extend(found)
        if task is not None:
            tasks.append(task)
    if problems:
        raise CorpusError(problems)
    return tasks


_GIT = [
    "git",
    "-c",
    "user.name=benchmark",
    "-c",
    "user.email=benchmark@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.autocrlf=false",
]


def _git(repo: Path, *args: str) -> None:
    try:
        result = subprocess.run(
            [*_GIT, *args], cwd=repo, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH") from exc
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")


def _rmtree(path: Path) -> None:
    if not path.exists():
        return

    def make_writable_and_retry(function: Callable[[str], Any], target: str, _: Any) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def prepare_workspace(task: Task, work_dir: Path) -> Path:
    """Copy a task's ``before`` tree into a fresh scratch Git repository."""
    repo = work_dir / task.task_id / "repo"
    _rmtree(repo.parent)
    shutil.copytree(
        task.before,
        repo,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache"),
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "--no-verify", "--allow-empty", "-m", "benchmark: before migration")
    return repo


def agent_runner(toolkit: Any | None = None) -> Runner:
    """Return a runner for the repository's current agent entry point."""

    def run(config: RunConfig, output_dir: Path) -> Any:
        from migration_agent.agent import run_migration

        parameters = inspect.signature(run_migration).parameters
        if toolkit is not None and len(parameters) >= 2:
            return run_migration(config, toolkit, output_dir)
        return run_migration(config)

    return run


def _target_tests(repo: str) -> TestReport:
    from migration_agent.sandbox.runner import run_tests

    return run_tests(repo)


def _oracle_count(spec_path: Path, repo: Path) -> int:
    from migration_agent.analysis import StaticAnalyzer
    from migration_agent.spec.loader import load_spec

    return StaticAnalyzer(load_spec(spec_path), repo).baseline_count()


@dataclass
class Hooks:
    """Injectable boundaries for tests, ablations, and alternate runners."""

    runner: Runner | None = None
    test_runner: TestRunner | None = None
    counter: Counter | None = None


@dataclass
class TaskOutcome:
    """A task score and the diagnostic information behind it."""

    result: TaskResult
    before: int = 0
    after: int = 0
    agent_success: bool | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    output_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.result.to_dict()
        data["completeness"] = round(data["completeness"], 2)
        data["test_pass_rate"] = round(data["test_pass_rate"], 2)
        data.update(
            before=self.before,
            after=self.after,
            agent_success=self.agent_success,
            error=self.error,
            warnings=list(self.warnings),
            seconds=round(self.seconds, 2),
            output_dir=self.output_dir,
        )
        return data


def _read_usage(task_out: Path, warnings: list[str]) -> dict[str, int] | None:
    path = task_out / "trajectory.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("not a JSON object")
        return log_usage(document)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        warnings.append(f"trajectory.json could not be read ({exc}); tokens and steps are 0")
        return None


def run_task(
    task: Task, base_config: RunConfig, hooks: Hooks, work_dir: Path, output_dir: Path
) -> TaskOutcome:
    """Run and score one task without allowing a task failure to stop the suite."""
    runner = hooks.runner or agent_runner()
    test_runner = hooks.test_runner or _target_tests
    counter = hooks.counter or _oracle_count
    task_out = output_dir / task.task_id
    _rmtree(task_out)
    task_out.mkdir(parents=True)
    outcome = TaskOutcome(
        TaskResult(task.task_id, 0.0, 0.0, 0, 0, FAILED), output_dir=str(task_out)
    )
    started = time.monotonic()
    run_steps = 0
    scored: tuple[float, float] | None = None
    try:
        repo = prepare_workspace(task, work_dir)
        outcome.before = counter(task.spec_path, repo)
        if outcome.before == 0:
            raise ValueError("the oracle finds no deprecated usage in before/; the task is invalid")
        if outcome.before != len(task.expected_call_sites):
            outcome.warnings.append(
                f"the oracle counts {outcome.before} deprecated usages but task.json lists "
                f"{len(task.expected_call_sites)} expected_call_sites"
            )
        config = dataclasses.replace(
            base_config, repo_path=str(repo), spec_path=str(task.spec_path)
        )
        run_result = runner(config, task_out)
        outcome.agent_success = bool(run_result)
        run_steps = int(getattr(run_result, "steps", 0) or 0)
        outcome.after = counter(task.spec_path, repo)
        report = test_runner(str(repo))
        scored = (completeness(outcome.before, outcome.after), test_pass_rate(report))
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"
        _log.warning("task %s failed: %s", task.task_id, outcome.error)

    usage = _read_usage(task_out, outcome.warnings)
    tokens = usage["tokens"] if usage else 0
    steps = usage["steps"] if usage else run_steps
    if scored is None:
        outcome.result = TaskResult(task.task_id, 0.0, 0.0, tokens, steps, FAILED)
    else:
        outcome.result = TaskResult(
            task.task_id, scored[0], scored[1], tokens, steps, verdict(*scored)
        )
    outcome.seconds = time.monotonic() - started
    (task_out / "result.json").write_text(
        json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return outcome


def run_suite_detailed(
    tasks_dir: str,
    base_config: RunConfig,
    hooks: Hooks | None = None,
    *,
    only: Iterable[str] | None = None,
    output_dir: str | Path | None = None,
    work_dir: str | Path | None = None,
    keep_work: bool = False,
) -> list[TaskOutcome]:
    """Run all tasks, optionally filtering by task id, and write reports."""
    hooks = hooks or Hooks()
    tasks = load_tasks(tasks_dir, default_spec=base_config.spec_path or None)
    if only is not None:
        wanted = set(only)
        unknown = sorted(wanted - {task.task_id for task in tasks})
        if unknown:
            raise CorpusError([f"unknown task id: {name}" for name in unknown])
        tasks = [task for task in tasks if task.task_id in wanted]

    out = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp("-migration-bench"))
    outcomes: list[TaskOutcome] = []
    try:
        for number, task in enumerate(tasks, start=1):
            _log.info("[%d/%d] %s", number, len(tasks), task.task_id)
            outcomes.append(run_task(task, base_config, hooks, scratch, out))
    finally:
        if work_dir is None and not keep_work:
            _rmtree(scratch)
    write_report(outcomes, out)
    return outcomes


def run_suite(
    tasks_dir: str,
    base_config: RunConfig,
    hooks: Hooks | None = None,
    *,
    only: Iterable[str] | None = None,
    output_dir: str | Path | None = None,
    work_dir: str | Path | None = None,
    keep_work: bool = False,
) -> list[TaskResult]:
    """Run the agent over every task and return one result per task."""
    return [
        outcome.result
        for outcome in run_suite_detailed(
            tasks_dir,
            base_config,
            hooks,
            only=only,
            output_dir=output_dir,
            work_dir=work_dir,
            keep_work=keep_work,
        )
    ]


_CSV_COLUMNS = [
    "task_id",
    "completeness",
    "test_pass_rate",
    "tokens",
    "steps",
    "verdict",
    "before",
    "after",
    "agent_success",
    "error",
]


def write_report(outcomes: list[TaskOutcome], out_dir: str | Path) -> None:
    """Write JSON summary and CSV task results."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        key: round(value, 2) if isinstance(value, float) else value
        for key, value in aggregate(outcome.result for outcome in outcomes).items()
    }
    rows = [outcome.to_dict() for outcome in outcomes]
    document = {"schema_version": SCHEMA_VERSION, "summary": summary, "tasks": rows}
    (out / "results.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with (out / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=_CSV_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
