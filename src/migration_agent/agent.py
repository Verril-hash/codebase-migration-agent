"""The agent loop: wires the nine modules together.

Owner: Verril (integration). Design: SDD figure 1 (architecture) and figure 4
(batch execution sequence). This is the only place that knows the order of
the modules; each module only knows its own job.

The loop takes its tools as a :class:`Toolkit`, so every step can be swapped
for a fake in tests, or for an ablation in the benchmark (for example running
without the repair loop). The defaults are the real modules.

Conventions the loop relies on:

* Edit functions (``apply_codemod``, ``propose_patch``, ``propose_fix``) write
  the change to the file and return the :class:`Patch` they made, or None when
  nothing applied. ``validate`` then checks the result.
* Paths are repository-relative everywhere, as in ``models.py``.
* Tests run in one of two stages. The baseline and every batch run on the
  **source** version of the library (SQLAlchemy 1.4), which accepts old and new
  style alike, so a half-migrated repository can be judged. The finished run is
  checked once more on the **target** version (2.0). Every run is judged against
  the baseline, so a test that already failed is not blamed on the migration,
  and a test that passed at baseline must still pass on the target.
* Lint must pass per batch (SDD section 7.2): a finding in an edited file that
  the baseline did not have counts as a failure, like a test.
* Run artifacts (graph memory, trajectory, final patch) are written to
  ``output_dir``, never inside the repository being migrated, so they cannot
  end up in its patch.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import (
    AnalysisResult,
    BatchPlan,
    FileState,
    LogEntry,
    MigrationSpec,
    Patch,
    RunConfig,
    Strategy,
    TestReport,
)


class Log(Protocol):
    def log_event(self, entry: LogEntry) -> None: ...

    def save(self, path: Path) -> None: ...


# ------------------------------------------------------------------ default tools
# Imported lazily so a module that is still a stub only fails when it is used.
def _load_spec(path: str) -> MigrationSpec:
    from migration_agent.spec.loader import load_spec

    return load_spec(path)


def _analyze(spec: MigrationSpec, config: RunConfig) -> AnalysisResult:
    from migration_agent.analysis import StaticAnalyzer

    return StaticAnalyzer(
        spec, config.repo_path, impact_depth_limit=config.impact_depth_limit
    ).analyze()


def _plan(analysis: AnalysisResult, batch_size: int) -> BatchPlan:
    from migration_agent.planning.planner import plan

    return plan(analysis.import_graph, analysis.impact_set, batch_size)


def _apply_codemod(rule, path: str, repo_path: str) -> Patch | None:
    from migration_agent.editing.engine import apply_codemod

    return apply_codemod(rule, path, repo_path=repo_path)


def _propose_patch(context: str, rule) -> Patch | None:
    from migration_agent.editing.engine import propose_patch

    return propose_patch(context, rule)


@functools.cache
def _llm():
    """One model client per process, so its token totals cover every fix."""
    from migration_agent.llm.client import LLMClient

    return LLMClient()


def _llm_usage() -> dict[str, int]:
    """Running totals of the shared client; empty when no model was called."""
    if not _llm.cache_info().currsize:
        return {}
    client = _llm()
    return {"calls": client.calls, "tokens_in": client.tokens_in, "tokens_out": client.tokens_out}


def apply_diff(diff: str, repo_path: str) -> bool:
    """Apply a unified diff to the working copy; False when it does not apply.

    ``--recount`` tolerates the wrong hunk line counts a model often writes.
    """
    result = subprocess.run(
        ["git", "-C", repo_path, "apply", "--recount", "--whitespace=nowarn", "-"],
        input=diff.encode("utf-8"),
        capture_output=True,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def _propose_fix(context: str, fix, repo_path: str) -> Patch | None:
    # Module 7 asks the model; the diff is applied here until Module 5 has a
    # function for applying a model's diff (agreed on PR #14).
    from migration_agent.correction.repair import request_fix
    from migration_agent.llm.client import LLMError

    try:
        proposal = request_fix(_llm(), context, fix)
    except LLMError:
        return None
    if proposal.patch is None or not apply_diff(proposal.patch.diff, repo_path):
        return None
    return proposal.patch


def _validate(patch: Patch, repo_path: str) -> bool:
    from migration_agent.editing.engine import validate

    return validate(patch, repo_path)


def _build_context(memory: GraphMemory, path: str, budget: int) -> str:
    from migration_agent.memory.context import build_context

    return build_context(memory, path, budget)


SOURCE, TARGET = "source", "target"
#: Sandbox profile of each stage. One migration family for now; a second one
#: (Python 3.8 to 3.12) adds its own pair.
STAGE_PROFILES = {SOURCE: "sqla-1.4", TARGET: "sqla-2.0"}


def stage_image(stage: str) -> str:
    """Image a stage runs in: ``SANDBOX_<STAGE>_IMAGE`` when set, else its profile's."""
    override = os.environ.get(f"SANDBOX_{stage.upper()}_IMAGE", "").strip()
    if override:
        return override
    from migration_agent.sandbox.image import image_tag

    return image_tag(STAGE_PROFILES[stage])


def _run_tests(repo_path: str, stage: str) -> TestReport:
    from migration_agent.sandbox.runner import run_tests

    return run_tests(repo_path, image=stage_image(stage))


def _run_lint(repo_path: str, files: list[str]) -> list[str]:
    from migration_agent.sandbox.runner import run_lint

    return run_lint(repo_path, files)


def _checkpoint(repo_path: str, message: str) -> str:
    from migration_agent.sandbox.git_tools import checkpoint

    return checkpoint(repo_path, message)


def _rollback(repo_path: str, commit: str) -> None:
    from migration_agent.sandbox.git_tools import rollback

    rollback(repo_path, commit)


def _diagnose(report: TestReport, analysis: AnalysisResult, memory: GraphMemory):
    from migration_agent.correction.diagnose import diagnose

    return diagnose(report, analysis, memory)


def _is_oscillating(history: list[str], new_patch: str) -> bool:
    from migration_agent.correction.diagnose import is_oscillating

    return is_oscillating(history, new_patch)


def _new_log() -> Log:
    from migration_agent.trajectory.log import TrajectoryLog

    return TrajectoryLog()


def _emit_patch(repo_path: str, base_commit: str, out_path: Path) -> Path:
    from migration_agent.trajectory.patch import emit_patch

    return emit_patch(repo_path, base_commit, out_path)


@dataclass
class Toolkit:
    """Every external step the loop takes; replace any of them for tests or ablations."""

    load_spec: Callable[[str], MigrationSpec] = _load_spec
    analyze: Callable[[MigrationSpec, RunConfig], AnalysisResult] = _analyze
    plan: Callable[[AnalysisResult, int], BatchPlan] = _plan
    apply_codemod: Callable[..., Patch | None] = _apply_codemod
    propose_patch: Callable[..., Patch | None] = _propose_patch
    propose_fix: Callable[..., Patch | None] = _propose_fix
    validate: Callable[[Patch, str], bool] = _validate
    build_context: Callable[[GraphMemory, str, int], str] = _build_context
    #: (repository, stage) with stage SOURCE or TARGET
    run_tests: Callable[[str, str], TestReport] = _run_tests
    run_lint: Callable[[str, list[str]], list[str]] = _run_lint
    checkpoint: Callable[[str, str], str] = _checkpoint
    rollback: Callable[[str, str], None] = _rollback
    diagnose: Callable[..., list] = _diagnose
    is_oscillating: Callable[[list[str], str], bool] = _is_oscillating
    new_log: Callable[[], Log] = _new_log
    emit_patch: Callable[[str, str, Path], Path] = _emit_patch
    llm_usage: Callable[[], dict[str, int]] = _llm_usage


# ------------------------------------------------------------------ result
@dataclass
class RunResult:
    """Outcome of one run. Truthy when every batch ended green."""

    success: bool
    dry_run: bool = False
    plan: BatchPlan = field(default_factory=BatchPlan)
    verified_batches: list[int] = field(default_factory=list)
    rolled_back_batches: list[int] = field(default_factory=list)
    steps: int = 0
    stopped_early: str = ""
    #: The suite on the target version: True green, False regressed, None not run.
    target_green: bool | None = None
    patch_path: Path | None = None
    output_dir: Path | None = None

    def __bool__(self) -> bool:
        return self.success


def _failing(report: TestReport) -> set[str]:
    return {failure.test_id for failure in report.failures}


def regressed(report: TestReport, baseline: TestReport) -> bool:
    """True when ``report`` fails a test that passed before migration."""
    if _failing(report) - _failing(baseline):
        return True
    return report.failed + report.errors > baseline.failed + baseline.errors


def regressions(report: TestReport, baseline: TestReport) -> TestReport:
    """``report`` without the failures the baseline already had, for diagnosis."""
    known = _failing(baseline)
    return dataclasses.replace(
        report, failures=[f for f in report.failures if f.test_id not in known]
    )


_POSITION = re.compile(r"^(?P<path>[^:]+):\d+:\d+: ")


def _lint_key(finding: str) -> str:
    """A finding without its line and column, which an edit elsewhere in the file shifts."""
    return _POSITION.sub(r"\g<path>: ", finding.strip())


def new_findings(findings: list[str], baseline: list[str]) -> list[str]:
    """Lint findings the baseline did not have."""
    known = {_lint_key(f) for f in baseline}
    return [f for f in findings if _lint_key(f) not in known]


class _Run:
    """State of one migration run; :func:`run_migration` is the public entry point."""

    def __init__(self, config: RunConfig, tools: Toolkit, output_dir: Path) -> None:
        self.config = config
        self.repo = config.repo_path
        self.tools = tools
        self.output_dir = output_dir
        self.log = tools.new_log()
        self.step = 0
        self.baseline = TestReport()
        self.lint_baseline: list[str] = []
        self.usage_at_start = tools.llm_usage()

    def record(
        self,
        module: str,
        action: str,
        *,
        batch_id: int | None = None,
        files: list[str] | None = None,
        status: str = "ok",
        **detail: Any,
    ) -> None:
        self.step += 1
        self.log.log_event(
            LogEntry(
                step_id=self.step,
                timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
                module=module,
                action=action,
                batch_id=batch_id,
                files=list(files or []),
                status=status,
                detail=dict(detail),
            )
        )

    def over_budget(self) -> bool:
        return self.step >= self.config.max_steps

    def broken(self, report: TestReport) -> bool:
        """A test regressed against the baseline, or an edit added a lint finding."""
        return regressed(report, self.baseline) or bool(report.lint_findings)

    def verify(self, edited: list[str], batch_id: int, attempt: int) -> TestReport:
        """Tests on the source version, plus lint of the edited files.

        The report's ``lint_findings`` hold only the findings the edits added.
        """
        report = self.tools.run_tests(self.repo, SOURCE)
        lint = self.tools.run_lint(self.repo, edited) if edited else []
        report = dataclasses.replace(report, lint_findings=new_findings(lint, self.lint_baseline))
        self.record(
            "verification",
            "tested",
            batch_id=batch_id,
            attempt=attempt,
            passed=report.passed,
            failed=report.failed,
            errors=report.errors,
            lint_findings=len(report.lint_findings),
        )
        return report

    # -------------------------------------------------------------- one batch
    def edit_file(self, path: str, memory: GraphMemory, rules: dict, batch_id: int) -> None:
        node = memory.get(path)
        for pattern_id in sorted({site.pattern_id for site in node.call_sites}):
            rule = rules.get(pattern_id)
            if rule is None:
                memory.add_pending_fix(path, f"no-rule:{pattern_id}")
                self.record(
                    "editing",
                    "no_rule",
                    batch_id=batch_id,
                    files=[path],
                    status="skipped",
                    pattern_id=pattern_id,
                )
                continue
            if rule.strategy is Strategy.CODEMOD:
                patch = self.tools.apply_codemod(rule, path, self.repo)
            else:
                context = self.tools.build_context(memory, path, self.config.token_budget)
                patch = self.tools.propose_patch(context, rule)
            if patch is None:
                self.record(
                    "editing",
                    "no_change",
                    batch_id=batch_id,
                    files=[path],
                    pattern_id=pattern_id,
                    strategy=rule.strategy.value,
                )
            elif self.tools.validate(patch, self.repo):
                memory.update_state(path, FileState.EDITED)
                self.record(
                    "editing",
                    "edited",
                    batch_id=batch_id,
                    files=[path],
                    pattern_id=pattern_id,
                    strategy=rule.strategy.value,
                )
            else:
                memory.add_pending_fix(path, f"invalid-edit:{pattern_id}")
                self.record(
                    "editing",
                    "invalid_edit",
                    batch_id=batch_id,
                    files=[path],
                    status="failed",
                    pattern_id=pattern_id,
                )

    def repair(
        self,
        report: TestReport,
        edited: list[str],
        analysis: AnalysisResult,
        memory: GraphMemory,
        batch_id: int,
    ) -> TestReport:
        history: list[str] = []
        for attempt in range(1, self.config.max_retries + 1):
            if not self.broken(report) or self.over_budget():
                break
            fixes = self.tools.diagnose(regressions(report, self.baseline), analysis, memory)
            self.record(
                "correction", "diagnosed", batch_id=batch_id, attempt=attempt, fixes=len(fixes)
            )
            applied = False
            for fix in fixes:
                if fix.file in memory:
                    memory.record_retry(fix.file)
                context = self.tools.build_context(memory, fix.file, self.config.token_budget)
                patch = self.tools.propose_fix(context, fix, self.repo)
                if patch is None:
                    self.record(
                        "correction",
                        "no_fix",
                        batch_id=batch_id,
                        files=[fix.file],
                        status="skipped",
                    )
                    continue
                if self.tools.is_oscillating(history, patch.diff):
                    self.record(
                        "correction",
                        "oscillation",
                        batch_id=batch_id,
                        files=[fix.file],
                        status="skipped",
                    )
                    continue
                history.append(patch.diff)
                if self.tools.validate(patch, self.repo):
                    applied = True
                    self.record(
                        "correction",
                        "fix_applied",
                        batch_id=batch_id,
                        files=[fix.file],
                        reason=fix.reason,
                    )
            if not applied:
                break
            report = self.verify(edited, batch_id, attempt)
        return report

    # -------------------------------------------------------------- whole run
    def run(self) -> RunResult:
        tools, config = self.tools, self.config
        spec = tools.load_spec(config.spec_path)
        analysis = tools.analyze(spec, config)
        self.record(
            "analysis",
            "analyzed",
            call_sites=len(analysis.call_sites),
            unresolved=len(analysis.unresolved_sites),
            parse_failures=len(analysis.parse_failures),
            impacted_files=len(analysis.impact_set.files),
        )
        batches = tools.plan(analysis, config.batch_size)
        self.record(
            "planning",
            "planned",
            batches=len(batches.batches),
            planned_files=sum(len(b.files) for b in batches.batches),
        )
        result = RunResult(success=True, plan=batches, output_dir=self.output_dir)
        if config.dry_run:
            result.dry_run = True
            return self.finish(result)

        memory = GraphMemory.from_analysis(analysis, self.repo)
        self.baseline = baseline = tools.run_tests(self.repo, SOURCE)
        # Only impacted files are ever edited, so only they need a lint baseline.
        impacted = sorted(analysis.impact_set.files)
        self.lint_baseline = tools.run_lint(self.repo, impacted) if impacted else []
        self.record(
            "verification",
            "baseline",
            passed=baseline.passed,
            failed=baseline.failed,
            errors=baseline.errors,
            lint_findings=len(self.lint_baseline),
        )
        base_commit = tools.checkpoint(self.repo, "migration: before")
        rules = {rule.pattern_id: rule for rule in spec.replacement_rules}

        for batch in batches.batches:
            if self.over_budget():
                result.stopped_early = f"step budget of {config.max_steps} reached"
                result.success = False
                break
            start = tools.checkpoint(self.repo, f"migration: before batch {batch.batch_id}")
            for path in batch.files:
                if path in memory:
                    self.edit_file(path, memory, rules, batch.batch_id)
            edited_now = set(memory.in_state(FileState.EDITED))
            edited = [path for path in batch.files if path in edited_now]
            report = self.verify(edited, batch.batch_id, attempt=0)
            report = self.repair(report, edited, analysis, memory, batch.batch_id)

            green = not self.broken(report)
            if green:
                tools.checkpoint(self.repo, f"migration: batch {batch.batch_id} verified")
                result.verified_batches.append(batch.batch_id)
            else:
                tools.rollback(self.repo, start)
                result.rolled_back_batches.append(batch.batch_id)
                result.success = False
            state = FileState.VERIFIED if green else FileState.ROLLED_BACK
            for path in batch.files:
                if path in memory:
                    memory.update_state(path, state)
            self.record(
                "verification",
                "batch_verified" if green else "batch_rolled_back",
                batch_id=batch.batch_id,
                files=batch.files,
                status="ok" if green else "failed",
            )
            memory.save(self.output_dir / "graph_memory.json")

        if not result.stopped_early:
            final = tools.run_tests(self.repo, TARGET)
            result.target_green = not regressed(final, baseline)
            result.success = result.success and result.target_green
            self.record(
                "verification",
                "target_tested",
                status="ok" if result.target_green else "failed",
                passed=final.passed,
                failed=final.failed,
                errors=final.errors,
                regressed=sorted(_failing(regressions(final, baseline))),
            )

        result.patch_path = tools.emit_patch(
            self.repo, base_commit, self.output_dir / "migration.patch"
        )
        return self.finish(result)

    def finish(self, result: RunResult) -> RunResult:
        self.record(
            "agent",
            "finished",
            status="ok" if result.success else "failed",
            verified=result.verified_batches,
            rolled_back=result.rolled_back_batches,
            stopped_early=result.stopped_early,
            target_green=result.target_green,
            llm=self.llm_spent(),
        )
        result.steps = self.step
        self.log.save(self.output_dir / "trajectory.json")
        return result

    def llm_spent(self) -> dict[str, int]:
        """Model calls and tokens of this run (the shared client also counts earlier runs)."""
        now = self.tools.llm_usage()
        return {key: value - self.usage_at_start.get(key, 0) for key, value in now.items()}


def run_migration(
    config: RunConfig, toolkit: Toolkit | None = None, output_dir: str | Path | None = None
) -> RunResult:
    """Run one migration end to end.

    Artifacts go to ``output_dir`` (default ``runs/<repository name>``). The
    result is truthy when every batch ended green.
    """
    out = Path(output_dir) if output_dir is not None else Path("runs") / Path(config.repo_path).name
    out.mkdir(parents=True, exist_ok=True)
    return _Run(config, toolkit or Toolkit(), out).run()
