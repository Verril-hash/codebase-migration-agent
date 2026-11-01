"""Order files over the dependency graph and group them into batches.

Owner: Verril (Module 3). Design: SDD section 5 and requirement FR6.

An edge ``a -> b`` in the import graph means ``a`` depends on ``b``, so ``b``
must be migrated first; otherwise ``a`` would be edited against a contract
that is about to change. Files that import each other form a strongly
connected component: no order of them is safe, so a component is always
edited and verified inside one batch, even if that makes the batch larger
than ``batch_size``. Splitting a cycle across batches is exactly the
circular regression the planner exists to prevent.

Only files in the impact set are planned. The order still comes from the
whole graph, so ``a -> x -> b`` keeps ``b`` before ``a`` even when the file
``x`` in the middle needs no edit.

The result is deterministic: ties are broken by file name.
"""

from __future__ import annotations

import networkx as nx

from migration_agent.models import Batch, BatchPlan, ImpactSet


def dependency_order(import_graph: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components of the graph, dependencies first.

    Each component is a sorted list of files; a file in no cycle is a
    component of one.
    """
    graph = nx.DiGraph()
    for source, targets in import_graph.items():
        graph.add_node(source)
        graph.add_edges_from((source, target) for target in targets if target != source)
    condensed = nx.condensation(graph)
    members = {node: sorted(condensed.nodes[node]["members"]) for node in condensed}
    # Reverse the edges so a dependency comes before the files that use it.
    reversed_dag = condensed.reverse(copy=False)
    order = nx.lexicographical_topological_sort(reversed_dag, key=lambda node: members[node][0])
    return [members[node] for node in order]


def plan(import_graph: dict[str, list[str]], impact_set: ImpactSet, batch_size: int) -> BatchPlan:
    """Dependencies before dependents; each cycle stays inside one batch."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    to_edit = set(impact_set.files)
    if not to_edit:
        return BatchPlan()

    # Files the analysis reached that are missing from the graph still get planned.
    graph = {path: list(deps) for path, deps in import_graph.items()}
    for path in to_edit:
        graph.setdefault(path, [])

    batches: list[Batch] = []
    current: list[str] = []
    for component in dependency_order(graph):
        unit = [path for path in component if path in to_edit]
        if not unit:
            continue
        if current and len(current) + len(unit) > batch_size:
            batches.append(Batch(len(batches), current))
            current = []
        current.extend(unit)
    if current:
        batches.append(Batch(len(batches), current))
    return BatchPlan(batches)
