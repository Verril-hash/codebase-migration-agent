"""Collect Python source files and apply exclude rules (sub-component 2.1).

Owner: Verril (Module 2). Design: Module 2 detailed design, class RepositoryScanner.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

#: Directories that never hold the project's own source code.
DEFAULT_EXCLUDES = (
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "site-packages",
    "*.egg-info",
)


class RepositoryScanner:
    """Find every ``.py`` file under ``root``, skipping excluded directories."""

    def __init__(self, root: str | Path, excludes: Iterable[str] = ()) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"repository root does not exist: {self.root}")
        self.excludes = tuple(DEFAULT_EXCLUDES) + tuple(excludes)

    def _excluded(self, relative: Path) -> bool:
        """True when any directory on the path, or the path itself, matches a pattern."""
        posix = relative.as_posix()
        if any(fnmatch(posix, pattern) for pattern in self.excludes):
            return True
        return any(
            fnmatch(part, pattern) for part in relative.parts[:-1] for pattern in self.excludes
        )

    def scan(self) -> list[str]:
        """Repository-relative POSIX paths of all source files, sorted for determinism."""
        found = []
        for path in self.root.rglob("*.py"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if not self._excluded(relative):
                found.append(relative.as_posix())
        return sorted(found)
