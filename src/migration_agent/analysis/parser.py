"""Parse source files into syntax trees, recording failures instead of aborting (2.2).

Owner: Verril (Module 2). Design: Module 2 detailed design, class SourceParser
and figure 2 (scan and parse flowchart).

The primary parser is the standard ``ast`` module. Files are read as bytes so
``ast`` honours a PEP 263 encoding cookie. A file that fails to parse becomes a
:class:`ParseFailure` and the analysis carries on. A fallback parser for
Python 2 sources plugs in through ``fallback``; until it exists, Python 2 files
are reported as parse failures.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from migration_agent.models import ParseFailure

#: Signature of a fallback parser: (source bytes, path) -> tree, or raise SyntaxError.
Fallback = Callable[[bytes, str], ast.Module]


@dataclass
class ParsedModule:
    """One successfully parsed file. Internal to Module 2; not a shared shape."""

    path: str  # repository-relative POSIX path, the node id everywhere else
    module: str  # dotted module name, e.g. "app.db.session"
    tree: ast.Module
    source_hash: str
    is_package: bool  # True for an __init__.py
    #: Names a package exports explicitly through ``__all__``, when it defines one.
    all_names: list[str] | None = field(default=None)
    #: True when only the fallback parser could read the file (Python 2 source).
    python2: bool = False


def module_name(relative_path: str, root: Path) -> str:
    """Dotted module name of a file, found by walking up through package directories.

    ``src/app/db/session.py`` becomes ``app.db.session`` when ``src/`` has no
    ``__init__.py`` but ``app/`` and ``app/db/`` do; ``app/__init__.py``
    becomes ``app``. A top-level script ``tool.py`` becomes ``tool``.
    """
    path = Path(relative_path)
    parts = [] if path.name == "__init__.py" else [path.stem]
    directory = path.parent
    while directory != Path(".") and (root / directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts)) or path.stem


def _all_names(tree: ast.Module) -> list[str] | None:
    """Literal ``__all__ = [...]`` or ``(...)`` at module level, else None."""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.List | ast.Tuple):
            names = [elt.value for elt in value.elts if isinstance(elt, ast.Constant)]
            return [n for n in names if isinstance(n, str)]
    return None


class SourceParser:
    def __init__(self, root: str | Path, fallback: Fallback | None = None) -> None:
        self.root = Path(root).resolve()
        self.fallback = fallback

    def parse(self, path: str) -> ParsedModule | ParseFailure:
        """Parse one repository-relative file."""
        try:
            data = (self.root / path).read_bytes()
        except OSError as error:
            return ParseFailure(path, f"unreadable: {error}")
        python2 = False
        try:
            tree = ast.parse(data, filename=path)
        except (SyntaxError, ValueError) as error:
            if self.fallback is None:
                return ParseFailure(path, f"syntax error: {error}")
            try:
                tree = self.fallback(data, path)
                python2 = True
            except (SyntaxError, ValueError) as fallback_error:
                return ParseFailure(path, f"syntax error (fallback too): {fallback_error}")
        return ParsedModule(
            path=path,
            module=module_name(path, self.root),
            tree=tree,
            source_hash=hashlib.sha256(data).hexdigest(),
            is_package=Path(path).name == "__init__.py",
            all_names=_all_names(tree),
            python2=python2,
        )

    def parse_all(self, paths: list[str]) -> tuple[dict[str, ParsedModule], list[ParseFailure]]:
        """Parse every path; returns parsed modules by path and the failures."""
        modules: dict[str, ParsedModule] = {}
        failures: list[ParseFailure] = []
        for path in paths:
            result = self.parse(path)
            if isinstance(result, ParseFailure):
                failures.append(result)
            else:
                modules[path] = result
        return modules, failures
