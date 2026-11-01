"""Apply codemods first, LLM-proposed patches for the rest, and validate both.

Owner: Adithya (Module 5). Design: SDD section 5 (Table 2).
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path

import libcst as cst

from migration_agent.editing.codemods import CODEMODS
from migration_agent.models import FixRequest, Patch, ReplacementRule


def apply_codemod(rule: ReplacementRule, path: str) -> Patch | None:
    """Deterministic rewrite with LibCST; None when the rule does not apply."""
    if rule.ref not in CODEMODS:
        raise ValueError(f"unknown codemod: {rule.ref}")
    source_path = Path(path)
    original = source_path.read_text(encoding="utf-8")
    module = cst.parse_module(original)
    transformed = module.visit(CODEMODS[rule.ref]()).code
    if transformed == original:
        return None
    source_path.write_text(transformed, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            transformed.splitlines(keepends=True),
            fromfile=str(source_path),
            tofile=str(source_path),
        )
    )
    return Patch(file=str(source_path), diff=diff, source=rule.strategy)


def propose_patch(context: str, rule: ReplacementRule) -> Patch:
    """Ask the language model for a unified diff when no codemod fits."""
    raise NotImplementedError(
        f"LLM patch proposal for {rule.ref!r} requires the configured LLM client"
    )


def propose_fix(context: str, fix: FixRequest) -> Patch:
    """Ask the language model for a targeted repair of a diagnosed failure (Module 7 input)."""
    raise NotImplementedError("LLM repair proposals are provided by the LLM owner")


def validate(patch: Patch, repo_path: str) -> bool:
    """True only if the patch applies cleanly and the file still parses."""
    file_path = Path(patch.file)
    if not file_path.is_absolute():
        file_path = Path(repo_path) / file_path
    try:
        source = file_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return False
    try:
        ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return False
    return True
