"""Tests for Module 3, the planner."""

from __future__ import annotations

import pytest

from migration_agent.models import ImpactSet
from migration_agent.planning.planner import dependency_order, plan


def files_of(result) -> list[list[str]]:
    return [batch.files for batch in result.batches]


def impact(*files: str) -> ImpactSet:
    return ImpactSet(files=list(files))


def position(order: list[list[str]], path: str) -> int:
    return next(i for i, component in enumerate(order) if path in component)


def test_dependencies_come_before_dependents() -> None:
    graph = {"api.py": ["service.py"], "service.py": ["db.py"], "db.py": []}
    result = plan(graph, impact("api.py", "service.py", "db.py"), batch_size=1)
    assert files_of(result) == [["db.py"], ["service.py"], ["api.py"]]
    assert [batch.batch_id for batch in result.batches] == [0, 1, 2]


def test_files_outside_the_impact_set_are_not_planned_but_still_order_others() -> None:
    # api -> middle -> db, and only api and db need edits.
    graph = {"api.py": ["middle.py"], "middle.py": ["db.py"], "db.py": [], "other.py": []}
    result = plan(graph, impact("api.py", "db.py"), batch_size=1)
    assert files_of(result) == [["db.py"], ["api.py"]]


def test_a_cycle_stays_in_one_batch_even_when_larger_than_batch_size() -> None:
    graph = {
        "a.py": ["b.py"],
        "b.py": ["c.py"],
        "c.py": ["a.py", "base.py"],
        "base.py": [],
        "top.py": ["a.py"],
    }
    result = plan(graph, impact("a.py", "b.py", "c.py", "base.py", "top.py"), batch_size=2)
    assert files_of(result) == [["base.py"], ["a.py", "b.py", "c.py"], ["top.py"]]


def test_only_impacted_members_of_a_cycle_are_planned() -> None:
    graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    assert files_of(plan(graph, impact("b.py"), batch_size=5)) == [["b.py"]]


def test_independent_files_are_packed_up_to_batch_size() -> None:
    graph = {name: [] for name in ("e.py", "d.py", "c.py", "b.py", "a.py")}
    result = plan(graph, impact(*graph), batch_size=2)
    assert files_of(result) == [["a.py", "b.py"], ["c.py", "d.py"], ["e.py"]]


def test_a_dependency_may_share_a_batch_with_its_dependent() -> None:
    graph = {"api.py": ["db.py"], "db.py": []}
    assert files_of(plan(graph, impact("api.py", "db.py"), batch_size=5)) == [["db.py", "api.py"]]


def test_order_is_deterministic_regardless_of_input_order() -> None:
    graph_a = {"x.py": ["lib.py"], "y.py": ["lib.py"], "lib.py": []}
    graph_b = dict(reversed(list(graph_a.items())))
    files = ("y.py", "x.py", "lib.py")
    assert plan(graph_a, impact(*files), 1) == plan(graph_b, impact(*reversed(files)), 1)


def test_files_missing_from_the_graph_and_self_imports_are_handled() -> None:
    graph = {"a.py": ["a.py"]}
    result = plan(graph, impact("a.py", "generated.py"), batch_size=5)
    assert files_of(result) == [["a.py", "generated.py"]]


def test_empty_impact_set_gives_an_empty_plan() -> None:
    assert plan({"a.py": []}, ImpactSet(), batch_size=3).batches == []


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        plan({}, impact("a.py"), batch_size=0)


def test_dependency_order_groups_each_cycle() -> None:
    order = dependency_order({"a.py": ["b.py"], "b.py": ["a.py", "c.py"], "c.py": []})
    assert order == [["c.py"], ["a.py", "b.py"]]
    assert position(order, "c.py") < position(order, "a.py")
