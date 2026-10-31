"""Turn a failing test report into targeted fix requests.

Owner: Prajwal (Module 7). Design: SDD section 5.3: parse the trace, map
frames to graph nodes, walk up to the nearest edited node.
"""

from __future__ import annotations

from migration_agent.memory.graph_memory import GraphMemory
from migration_agent.models import AnalysisResult, FixRequest, TestReport


def diagnose(report: TestReport, analysis: AnalysisResult, memory: GraphMemory) -> list[FixRequest]:
    raise NotImplementedError("Module 7 (Prajwal): diagnose")


def is_oscillating(patch_history: list[str], new_patch: str) -> bool:
    """True when a new patch repeats an earlier, already rejected one."""
    raise NotImplementedError("Module 7 (Prajwal): is_oscillating")
