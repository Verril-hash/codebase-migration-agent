"""Tests for the agent loop, with every module replaced by a fake.

The loop's job is ordering and bookkeeping: edit each batch, verify it, repair
or roll it back, record every step. These tests check exactly that, without
SQLAlchemy, Docker, Git or a language model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from migration_agent.agent import RunResult, Toolkit, regressed, run_migration
from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import (
    AnalysisResult,
    Batch,
    BatchPlan,
    CallSite,
    DeprecatedPattern,
    FileState,
    FixRequest,
    ImpactSet,
    MigrationSpec,
    Patch,
    ReplacementRule,
    RunConfig,
    Strategy,
    TestFailure,
    TestReport,
)

GREEN = TestReport(passed=5)


def red(*test_ids: str) -> TestReport:
    return TestReport(
        passed=5 - len(test_ids),
        failed=len(test_ids),
        failures=[TestFailure(t, "boom", "Traceback ...") for t in test_ids],
    )


SPEC = MigrationSpec(
    "sqla",
    "SQLAlchemy 1.4 to 2.0",
    "2.0",
    deprecated_patterns=[
        DeprecatedPattern("base", "sqlalchemy.ext.declarative.declarative_base"),
        DeprecatedPattern("query", "sqlalchemy.orm.Session.query"),
        DeprecatedPattern("orphan", "sqlalchemy.engine.Engine.execute"),
    ],
    replacement_rules=[
        ReplacementRule("base", Strategy.CODEMOD, "sqla_declarative_base"),
        ReplacementRule("query", Strategy.LLM, "sqla_session_query"),
    ],
)

ANALYSIS = AnalysisResult(
    import_graph={"db.py": [], "api.py": ["db.py"], "util.py": []},
    call_sites=[
        CallSite("db.py", 3, 0, "db.<module>", "base"),
        CallSite("api.py", 7, 4, "api.list_users", "query"),
    ],
    impact_set=ImpactSet(functions=["db.<module>", "api.list_users"], files=["api.py", "db.py"]),
)
PLAN = BatchPlan([Batch(0, ["db.py"]), Batch(1, ["api.py"])])


class FakeLog:
    def __init__(self) -> None:
        self.entries = []
        self.saved_to: Path | None = None

    def log_event(self, entry) -> None:
        self.entries.append(entry)

    def save(self, path: Path) -> None:
        self.saved_to = path

    def actions(self) -> list[str]:
        return [e.action for e in self.entries]


class World:
    """Records what the loop asked the fake tools to do."""

    def __init__(self, reports: Iterable[TestReport], analysis: AnalysisResult = ANALYSIS) -> None:
        self.reports = iter(reports)
        self.analysis = analysis
        self.log = FakeLog()
        self.commits: list[str] = []
        self.rollbacks: list[str] = []
        self.edits: list[tuple[str, str]] = []
        self.fixes: list[str] = []
        self.fix_patches: list[str] = []

    def checkpoint(self, repo: str, message: str) -> str:
        self.commits.append(message)
        return f"c{len(self.commits) - 1}"

    def toolkit(self, **overrides) -> Toolkit:
        tools = Toolkit(
            load_spec=lambda path: SPEC,
            analyze=lambda spec, config: self.analysis,
            plan=lambda analysis, size: PLAN,
            apply_codemod=lambda rule, path, repo: self._edit("codemod", path),
            propose_patch=lambda context, rule: self._edit("llm", context),
            propose_fix=lambda context, fix: self._fix(fix),
            validate=lambda patch, repo: True,
            build_context=lambda memory, path, budget: path,
            run_tests=lambda repo: next(self.reports),
            checkpoint=self.checkpoint,
            rollback=lambda repo, commit: self.rollbacks.append(commit),
            diagnose=lambda report, analysis, memory: [
                FixRequest("api.py", "api.list_users", report.failures[0], "contract changed")
            ],
            is_oscillating=lambda history, diff: diff in history,
            new_log=lambda: self.log,
            emit_patch=lambda repo, base, out: out,
        )
        for name, value in overrides.items():
            setattr(tools, name, value)
        return tools

    def _edit(self, how: str, path: str) -> Patch:
        self.edits.append((how, path))
        return Patch(path, f"--- edit {path}", Strategy.CODEMOD)

    def _fix(self, fix: FixRequest) -> Patch:
        diff = self.fix_patches.pop(0) if self.fix_patches else f"fix {len(self.fixes)}"
        self.fixes.append(diff)
        return Patch(fix.file, diff, Strategy.LLM)


def config(tmp_path: Path, **overrides) -> RunConfig:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return RunConfig(repo_path=str(repo), spec_path="spec.json", **overrides)


def run(world: World, tmp_path: Path, tools: Toolkit | None = None, **config_overrides):
    out = tmp_path / "out"
    result = run_migration(config(tmp_path, **config_overrides), tools or world.toolkit(), out)
    memory = (
        GraphMemory.load(out / "graph_memory.json")
        if (out / "graph_memory.json").exists()
        else None
    )
    return result, memory


# ------------------------------------------------------------------ happy path
def test_every_batch_is_edited_verified_and_logged(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])  # baseline, batch 0, batch 1
    result, memory = run(world, tmp_path)

    assert result and result.verified_batches == [0, 1] and result.rolled_back_batches == []
    assert world.edits == [("codemod", "db.py"), ("llm", "api.py")]  # rule strategy is honoured
    assert world.commits == [
        "migration: before",
        "migration: before batch 0",
        "migration: batch 0 verified",
        "migration: before batch 1",
        "migration: batch 1 verified",
    ]
    assert memory.get_state("db.py") is FileState.VERIFIED
    assert memory.get_state("api.py") is FileState.VERIFIED
    assert result.patch_path == tmp_path / "out" / "migration.patch"
    assert world.log.saved_to == tmp_path / "out" / "trajectory.json"
    assert world.log.actions()[:3] == ["analyzed", "planned", "baseline"]
    assert world.log.actions()[-1] == "finished"
    assert [e.step_id for e in world.log.entries] == list(range(1, len(world.log.entries) + 1))
    assert result.steps == len(world.log.entries)


def test_artifacts_never_go_inside_the_repository(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    run(world, tmp_path)
    assert list((tmp_path / "repo").iterdir()) == []


def test_dry_run_analyses_and_plans_without_touching_anything(tmp_path: Path) -> None:
    world = World([])
    result, _ = run(world, tmp_path, dry_run=True)
    assert result.dry_run and result.success and result.plan == PLAN
    assert world.edits == [] and world.commits == []
    assert world.log.actions() == ["analyzed", "planned", "finished"]


# ------------------------------------------------------------------ repair loop
def test_a_failing_batch_is_repaired_and_then_verified(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, red("test_api"), GREEN])
    result, memory = run(world, tmp_path)
    assert result.verified_batches == [0, 1]
    assert world.fixes == ["fix 0"]
    assert memory.get("api.py").retry_count == 1
    assert "fix_applied" in world.log.actions()


def test_a_batch_that_cannot_be_repaired_is_rolled_back(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, red("t"), red("t"), red("t"), red("t")])
    result, memory = run(world, tmp_path, max_retries=3)
    assert not result
    assert result.verified_batches == [0] and result.rolled_back_batches == [1]
    assert world.rollbacks == ["c3"]  # the checkpoint taken before batch 1
    assert len(world.fixes) == 3
    assert memory.get_state("api.py") is FileState.ROLLED_BACK
    assert memory.get_state("db.py") is FileState.VERIFIED


def test_a_repeated_fix_is_treated_as_oscillation(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, red("t"), red("t"), red("t")])
    world.fix_patches = ["same diff", "same diff", "same diff"]
    result, _ = run(world, tmp_path, max_retries=3)
    assert result.rolled_back_batches == [1]
    assert world.fixes == ["same diff", "same diff"]  # the repeat ends the repair attempts
    assert "oscillation" in world.log.actions()


def test_a_test_that_failed_before_migration_is_not_blamed_on_it(tmp_path: Path) -> None:
    world = World([red("flaky"), red("flaky"), red("flaky")])
    result, _ = run(world, tmp_path)
    assert result.verified_batches == [0, 1] and world.fixes == []


def test_regressed_compares_against_the_baseline() -> None:
    assert not regressed(GREEN, GREEN)
    assert regressed(red("a"), GREEN)
    assert not regressed(red("a"), red("a"))
    assert regressed(red("a", "b"), red("a"))
    assert regressed(TestReport(errors=1), GREEN)  # a collection error with no details


# ------------------------------------------------------------------ edge cases
def test_a_pattern_without_a_rule_is_recorded_not_crashed(tmp_path: Path) -> None:
    analysis = AnalysisResult(
        import_graph={"db.py": []},
        call_sites=[CallSite("db.py", 1, 0, "db.<module>", "orphan")],
        impact_set=ImpactSet(files=["db.py"]),
    )
    world = World([GREEN, GREEN], analysis)
    tools = world.toolkit(plan=lambda analysis, size: BatchPlan([Batch(0, ["db.py"])]))
    result, memory = run(world, tmp_path, tools)
    assert result.success and world.edits == []
    assert "no_rule" in world.log.actions()


def test_an_invalid_edit_is_not_marked_edited(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    tools = world.toolkit(validate=lambda patch, repo: False)
    run(world, tmp_path, tools)
    assert world.log.actions().count("invalid_edit") == 2


def test_the_step_budget_stops_the_run(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    result, _ = run(world, tmp_path, max_steps=3)
    assert not result and "step budget" in result.stopped_early
    assert result.verified_batches == []


def test_run_result_is_truthy_only_on_success() -> None:
    assert RunResult(success=True)
    assert not RunResult(success=False)


def test_the_log_records_the_analysis_summary(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    run(world, tmp_path)
    analyzed = world.log.entries[0]
    assert analyzed.module == "analysis" and analyzed.detail["call_sites"] == 2
    json.dumps([entry.to_dict() for entry in world.log.entries])  # serialisable for M8


@pytest.mark.parametrize("missing", ["run_tests", "checkpoint"])
def test_default_tools_report_the_module_that_is_missing(tmp_path: Path, missing: str) -> None:
    world = World([GREEN, GREEN, GREEN])
    tools = world.toolkit()
    setattr(tools, missing, getattr(Toolkit(), missing))  # the real, still-stubbed module
    with pytest.raises(NotImplementedError, match="Module 6"):
        run(world, tmp_path, tools)
