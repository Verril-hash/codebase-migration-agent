"""Tests for the agent loop, with every module replaced by a fake.

The loop's job is ordering and bookkeeping: edit each batch, verify it, repair
or roll it back, record every step. These tests check exactly that, without
SQLAlchemy, Docker, Git or a language model.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest

from migration_agent import agent
from migration_agent.agent import (
    SOURCE,
    TARGET,
    RunResult,
    Toolkit,
    apply_diff,
    new_findings,
    regressed,
    run_migration,
)
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

    def __init__(
        self,
        reports: Iterable[TestReport],
        analysis: AnalysisResult = ANALYSIS,
        lint: Iterable[list[str]] = (),
    ) -> None:
        self.reports = iter(reports)  # source-version runs, in order
        self.target = GREEN  # the one run on the target version
        self.lint_results = iter(lint)  # lint runs, in order; clean once used up
        self.analysis = analysis
        self.stages: list[str] = []
        self.lint_calls: list[list[str]] = []
        self.diagnosed: list[TestReport] = []
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
            propose_fix=lambda context, fix, repo: self._fix(fix),
            validate=lambda patch, repo: True,
            build_context=lambda memory, path, budget: path,
            run_tests=lambda repo, stage: self._test(stage),
            run_lint=lambda repo, files: self._lint(files),
            checkpoint=self.checkpoint,
            rollback=lambda repo, commit: self.rollbacks.append(commit),
            diagnose=self._diagnose,
            is_oscillating=lambda history, diff: diff in history,
            new_log=lambda: self.log,
            emit_patch=lambda repo, base, out: out,
            llm_usage=dict,
        )
        for name, value in overrides.items():
            setattr(tools, name, value)
        return tools

    def _test(self, stage: str) -> TestReport:
        self.stages.append(stage)
        return self.target if stage == TARGET else next(self.reports)

    def _lint(self, files: list[str]) -> list[str]:
        self.lint_calls.append(list(files))
        return next(self.lint_results, [])

    def _diagnose(self, report: TestReport, analysis, memory) -> list[FixRequest]:
        self.diagnosed.append(report)
        failure = report.failures[0] if report.failures else TestFailure("lint", "lint", "")
        return [FixRequest("api.py", "api.list_users", failure, "contract changed")]

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
    assert world.log.actions()[-2:] == ["target_tested", "finished"]
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


# ------------------------------------------------------------------ stages
def test_batches_run_on_the_source_version_and_the_finished_run_on_the_target(
    tmp_path: Path,
) -> None:
    world = World([GREEN, GREEN, GREEN])
    result, _ = run(world, tmp_path)
    assert world.stages == [SOURCE, SOURCE, SOURCE, TARGET]
    assert result and result.target_green is True


def test_a_regression_on_the_target_version_fails_the_run(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    world.target = red("test_api")  # passes on 1.4, fails on 2.0: a site was missed
    result, _ = run(world, tmp_path)
    assert not result and result.target_green is False
    assert result.verified_batches == [0, 1]  # each batch was fine on the source version
    final = next(e for e in world.log.entries if e.action == "target_tested")
    assert final.status == "failed" and final.detail["regressed"] == ["test_api"]


def test_a_test_failing_at_baseline_does_not_fail_the_target_run(tmp_path: Path) -> None:
    world = World([red("flaky"), red("flaky"), red("flaky")])
    world.target = red("flaky")
    result, _ = run(world, tmp_path)
    assert result and result.target_green is True


def test_no_target_run_when_the_step_budget_stopped_the_run(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    result, _ = run(world, tmp_path, max_steps=3)
    assert TARGET not in world.stages and result.target_green is None


def test_only_regressions_are_sent_to_diagnosis(tmp_path: Path) -> None:
    world = World([red("flaky"), red("flaky"), red("flaky", "test_api"), red("flaky")])
    result, _ = run(world, tmp_path)
    assert result.verified_batches == [0, 1]
    assert [f.test_id for f in world.diagnosed[0].failures] == ["test_api"]


# ------------------------------------------------------------------ lint
UNDEFINED = "api.py:7:5: F821 Undefined name `select`"


def test_lint_runs_on_the_impacted_files_then_on_each_batch_s_edits(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN])
    run(world, tmp_path)
    assert world.lint_calls == [["api.py", "db.py"], ["db.py"], ["api.py"]]


def test_a_lint_finding_added_by_an_edit_is_repaired(tmp_path: Path) -> None:
    world = World([GREEN, GREEN, GREEN, GREEN], lint=[[], [], [UNDEFINED], []])
    result, _ = run(world, tmp_path)
    assert result.verified_batches == [0, 1] and world.fixes == ["fix 0"]
    assert world.diagnosed[0].lint_findings == [UNDEFINED]


def test_a_lint_finding_that_cannot_be_repaired_rolls_the_batch_back(tmp_path: Path) -> None:
    world = World([GREEN] * 6, lint=[[], [], [UNDEFINED], [UNDEFINED], [UNDEFINED], [UNDEFINED]])
    result, _ = run(world, tmp_path, max_retries=3)
    assert result.rolled_back_batches == [1] and not result


def test_a_lint_finding_the_baseline_had_is_not_new_even_if_it_moved() -> None:
    before = ["api.py:3:1: F821 Undefined name `x`"]
    assert new_findings(["api.py:9:1: F821 Undefined name `x`"], before) == []
    assert new_findings([UNDEFINED], before) == [UNDEFINED]


# ------------------------------------------------------------------ default tools
def test_default_run_tests_uses_the_stage_s_image(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        "migration_agent.sandbox.runner.run_tests",
        lambda repo, image=None: calls.append(image) or GREEN,
    )
    monkeypatch.setitem(
        sys.modules,
        "migration_agent.sandbox.image",
        SimpleNamespace(image_tag=lambda profile: f"sandbox:{profile}"),
    )
    monkeypatch.delenv("SANDBOX_SOURCE_IMAGE", raising=False)
    monkeypatch.setenv("SANDBOX_TARGET_IMAGE", "custom:2.0")
    Toolkit().run_tests("repo", SOURCE)
    Toolkit().run_tests("repo", TARGET)
    assert calls == ["sandbox:sqla-1.4", "custom:2.0"]


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = session.query(User)\ny = 2\n", encoding="utf-8")
    return repo


# The hunk counts are wrong on purpose, as a model often writes them.
FIX = """--- a/a.py
+++ b/a.py
@@ -1,9 +1,9 @@
-x = session.query(User)
+x = session.scalars(select(User))
 y = 2
"""


def test_apply_diff_applies_a_model_s_diff_with_wrong_counts(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    assert apply_diff(FIX, str(repo))
    assert (repo / "a.py").read_text(encoding="utf-8").startswith("x = session.scalars(")
    assert not apply_diff(FIX, str(repo))  # already applied: the context no longer matches


@pytest.fixture
def fake_repair(monkeypatch: pytest.MonkeyPatch):
    """Module 7 and the LLM client replaced, so no model is called."""

    class LLMError(RuntimeError):
        pass

    state = SimpleNamespace(reply=None, raise_error=False)

    def request_fix(client, context, fix):
        if state.raise_error:
            raise LLMError("rate limited")
        return SimpleNamespace(patch=state.reply)

    monkeypatch.setitem(
        sys.modules, "migration_agent.correction.repair", SimpleNamespace(request_fix=request_fix)
    )
    monkeypatch.setitem(
        sys.modules,
        "migration_agent.llm.client",
        SimpleNamespace(LLMClient=lambda: object(), LLMError=LLMError),
    )
    agent._llm.cache_clear()
    yield state
    agent._llm.cache_clear()


def test_default_propose_fix_applies_module_7_s_diff(tmp_path: Path, fake_repair) -> None:
    repo = _git_repo(tmp_path)
    fix = FixRequest("a.py", "", TestFailure("t", "boom", ""), "reason")
    fake_repair.reply = Patch("a.py", FIX, Strategy.LLM)
    patch = Toolkit().propose_fix("context", fix, str(repo))
    assert patch is fake_repair.reply
    assert "scalars" in (repo / "a.py").read_text(encoding="utf-8")


def test_default_propose_fix_returns_none_when_there_is_no_usable_fix(
    tmp_path: Path, fake_repair
) -> None:
    repo = _git_repo(tmp_path)
    fix = FixRequest("a.py", "", TestFailure("t", "boom", ""), "reason")
    fake_repair.reply = None  # Module 7 rejected the reply
    assert Toolkit().propose_fix("context", fix, str(repo)) is None
    fake_repair.reply = Patch("a.py", FIX.replace("y = 2", "z = 3"), Strategy.LLM)
    assert Toolkit().propose_fix("context", fix, str(repo)) is None  # does not apply
    fake_repair.raise_error = True
    assert Toolkit().propose_fix("context", fix, str(repo)) is None  # the model call failed
    assert "query" in (repo / "a.py").read_text(encoding="utf-8")  # nothing changed
