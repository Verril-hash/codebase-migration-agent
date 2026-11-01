"""Load and validate a migration specification and the run configuration.

Owner: Adithya (Module 1). Design: SDD section 5 (Table 2) and section 6.2.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from migration_agent.models import (
    DeprecatedPattern,
    MigrationSpec,
    ReplacementRule,
    RunConfig,
    Strategy,
)


def _required_string(data: dict[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _list_of_dicts(data: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = data.get(field_name, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list of objects")
    return value


def load_spec(path: str | Path) -> MigrationSpec:
    """Read a spec file from ``specs/`` and validate every field.

    Raise ``ValueError`` naming the bad field when the file is invalid.
    """
    spec_path = Path(path)
    if not spec_path.is_file():
        raise FileNotFoundError(f"specification file not found: {spec_path}")
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in specification: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ValueError("specification must be a JSON object")

    patterns = []
    pattern_ids: set[str] = set()
    for index, item in enumerate(_list_of_dicts(raw, "deprecated_patterns")):
        prefix = f"deprecated_patterns[{index}]"
        pattern_id = _required_string(item, "pattern_id")
        if pattern_id in pattern_ids:
            raise ValueError(f"{prefix}.pattern_id must be unique")
        pattern_ids.add(pattern_id)
        patterns.append(
            DeprecatedPattern(
                pattern_id=pattern_id,
                ast_matcher=_required_string(item, "ast_matcher"),
                description=item.get("description", ""),
            )
        )
        if not isinstance(patterns[-1].description, str):
            raise ValueError(f"{prefix}.description must be a string")

    rules = []
    rule_ids: set[str] = set()
    for index, item in enumerate(_list_of_dicts(raw, "replacement_rules")):
        prefix = f"replacement_rules[{index}]"
        pattern_id = _required_string(item, "pattern_id")
        if pattern_id not in pattern_ids:
            raise ValueError(f"{prefix}.pattern_id references an unknown pattern")
        if pattern_id in rule_ids:
            raise ValueError(f"{prefix}.pattern_id must be unique")
        rule_ids.add(pattern_id)
        strategy_name = _required_string(item, "strategy")
        try:
            strategy = Strategy(strategy_name)
        except ValueError as exc:
            allowed = ", ".join(strategy.value for strategy in Strategy)
            raise ValueError(f"{prefix}.strategy must be one of: {allowed}") from exc
        rules.append(
            ReplacementRule(
                pattern_id=pattern_id,
                strategy=strategy,
                ref=_required_string(item, "ref"),
            )
        )

    oracle = raw.get("oracle", "")
    if not isinstance(oracle, str):
        raise ValueError("oracle must be a string")
    return MigrationSpec(
        spec_id=_required_string(raw, "spec_id"),
        title=_required_string(raw, "title"),
        target_version=_required_string(raw, "target_version"),
        deprecated_patterns=patterns,
        replacement_rules=rules,
        oracle=oracle,
    )


def load_config(repo_path: str, spec_path: str, **overrides: object) -> RunConfig:
    """Build the run configuration from CLI arguments and an optional config file."""
    repo = Path(repo_path)
    spec = Path(spec_path)
    if not repo.is_dir():
        raise NotADirectoryError(f"repository directory not found: {repo}")
    if not spec.is_file():
        raise FileNotFoundError(f"specification file not found: {spec}")

    valid_fields = {item.name for item in fields(RunConfig)}
    unknown = set(overrides) - valid_fields
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown run configuration field(s): {names}")
    values: dict[str, object] = {
        "repo_path": str(repo.resolve()),
        "spec_path": str(spec.resolve()),
    }
    for name, value in overrides.items():
        if name in {"repo_path", "spec_path"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            values[name] = value
        elif name == "dry_run":
            if not isinstance(value, bool):
                raise ValueError("dry_run must be a boolean")
            values[name] = value
        else:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            values[name] = value
    return RunConfig(**values)
