"""Load and validate a migration specification and the run configuration.

Owner: Adithya (Module 1). Design: SDD section 5 (Table 2) and section 6.2.
"""

from __future__ import annotations

from pathlib import Path

from migration_agent.models import MigrationSpec, RunConfig


def load_spec(path: str | Path) -> MigrationSpec:
    """Read a spec file from ``specs/`` and validate every field.

    Raise ``ValueError`` naming the bad field when the file is invalid.
    """
    raise NotImplementedError("Module 1 (Adithya): load_spec")


def load_config(repo_path: str, spec_path: str, **overrides: object) -> RunConfig:
    """Build the run configuration from CLI arguments and an optional config file."""
    raise NotImplementedError("Module 1 (Adithya): load_config")
