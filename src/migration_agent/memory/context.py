"""Assemble a token-bounded prompt context for one file.

Owner: Prabanjan (Module 4). Design: SDD section 5.2: the target file, its
neighbours, open failures and summaries, cut to the token budget.
"""

from __future__ import annotations

from migration_agent.memory.graph_memory import GraphMemory


def build_context(memory: GraphMemory, path: str, token_budget: int) -> str:
    raise NotImplementedError("Module 4 (Prabanjan): build_context")
