"""Run the agent over every task in ``corpus/`` and collect metrics.

Owner: Prabanjan (Module 9).
"""

from __future__ import annotations

from migration_agent.models import RunConfig, TaskResult


def run_suite(tasks_dir: str, base_config: RunConfig) -> list[TaskResult]:
    raise NotImplementedError("Module 9 (Prabanjan): run_suite")
