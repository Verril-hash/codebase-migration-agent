"""Order files over the dependency graph and group them into batches.

Owner: Verril (Module 3). Design: SDD section 5; cycles are collapsed into
strongly connected components and edited as one unit.
"""

from __future__ import annotations

from migration_agent.models import BatchPlan, ImpactSet


def plan(import_graph: dict[str, list[str]], impact_set: ImpactSet, batch_size: int) -> BatchPlan:
    """Dependencies before dependents; each cycle stays inside one batch."""
    raise NotImplementedError("Module 3 (Verril): plan")
