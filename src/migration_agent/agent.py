"""The agent loop: wires the nine modules together.

Owner: Verril (integration). Design: SDD figure 1 (architecture) and figure 4
(batch execution sequence). This function is the only place that knows the
order of the modules; each module only knows its own job.

Until the modules are implemented, calling :func:`run_migration` stops at the
first ``NotImplementedError``, which names the module and its owner.
"""

from __future__ import annotations

from datetime import UTC, datetime

from migration_agent.analysis import StaticAnalyzer
from migration_agent.correction.diagnose import diagnose, is_oscillating
from migration_agent.editing.engine import apply_codemod, propose_fix, propose_patch, validate
from migration_agent.memory.context import build_context
from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import FileState, LogEntry, RunConfig, Strategy
from migration_agent.planning.planner import plan
from migration_agent.sandbox import git_tools
from migration_agent.sandbox.runner import run_tests
from migration_agent.spec.loader import load_spec
from migration_agent.trajectory.log import TrajectoryLog


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_migration(config: RunConfig) -> bool:
    """Run one migration end to end; True when the final suite is green."""
    log = TrajectoryLog()
    step = 0

    def record(module: str, action: str, **detail: object) -> None:
        nonlocal step
        step += 1
        log.log_event(LogEntry(step, _now(), module, action, detail=dict(detail)))

    # Modules 1 to 3: prepare, once per run.
    spec = load_spec(config.spec_path)
    analysis = StaticAnalyzer(spec, config.repo_path).analyze()
    record("analysis", "analyzed", call_sites=len(analysis.call_sites))
    batches = plan(analysis.import_graph, analysis.impact_set, config.batch_size)
    record("planning", "planned", batches=len(batches.batches))
    if config.dry_run:
        return True

    memory = GraphMemory.from_analysis(analysis)
    rules = {rule.pattern_id: rule for rule in spec.replacement_rules}
    base = git_tools.checkpoint(config.repo_path, "before migration")

    # Modules 5 to 7: edit, verify and repair, once per batch.
    for batch in batches.batches:
        start = git_tools.checkpoint(config.repo_path, f"before batch {batch.batch_id}")
        for path in batch.files:
            for site in memory.get(path).call_sites:
                rule = rules[site.pattern_id]
                patch = apply_codemod(rule, path) if rule.strategy is Strategy.CODEMOD else None
                if patch is None:
                    context = build_context(memory, path, config.token_budget)
                    patch = propose_patch(context, rule)
                if validate(patch, config.repo_path):
                    memory.update_state(path, FileState.EDITED)

        history: list[str] = []
        report = run_tests(config.repo_path)
        retries = 0
        while not report.green and retries < config.max_retries:
            for fix in diagnose(report, analysis, memory):
                context = build_context(memory, fix.file, config.token_budget)
                patch = propose_fix(context, fix)
                if is_oscillating(history, patch.diff):
                    break
                history.append(patch.diff)
                validate(patch, config.repo_path)
            report = run_tests(config.repo_path)
            retries += 1

        final = FileState.VERIFIED if report.green else FileState.ROLLED_BACK
        if not report.green:
            git_tools.rollback(config.repo_path, start)
        for path in batch.files:
            memory.update_state(path, final)
        record("verification", "batch_done", batch=batch.batch_id, green=report.green)

    record("trajectory", "finished", base=base)
    return run_tests(config.repo_path).green
