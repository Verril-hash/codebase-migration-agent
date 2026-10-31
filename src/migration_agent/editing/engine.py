"""Apply codemods first, LLM-proposed patches for the rest, and validate both.

Owner: Adithya (Module 5). Design: SDD section 5 (Table 2).
"""

from __future__ import annotations

from migration_agent.models import FixRequest, Patch, ReplacementRule


def apply_codemod(rule: ReplacementRule, path: str) -> Patch | None:
    """Deterministic rewrite with LibCST; None when the rule does not apply."""
    raise NotImplementedError("Module 5 (Adithya): apply_codemod")


def propose_patch(context: str, rule: ReplacementRule) -> Patch:
    """Ask the language model for a unified diff when no codemod fits."""
    raise NotImplementedError("Module 5 (Adithya): propose_patch")


def propose_fix(context: str, fix: FixRequest) -> Patch:
    """Ask the language model for a targeted repair of a diagnosed failure (Module 7 input)."""
    raise NotImplementedError("Module 5 (Adithya): propose_fix")


def validate(patch: Patch, repo_path: str) -> bool:
    """True only if the patch applies cleanly and the file still parses."""
    raise NotImplementedError("Module 5 (Adithya): validate")
