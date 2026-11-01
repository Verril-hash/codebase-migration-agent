"""Build the file dependency graph from import statements (sub-component 2.3).

Owner: Verril (Module 2). Design: Module 2 detailed design, class ImportGraphBuilder.

An edge ``a.py -> b.py`` means ``a.py`` imports something defined in ``b.py``,
so ``b.py`` must be migrated first. Only files inside the repository become
nodes; imports of third-party packages are not edges. Imports anywhere in a
file count, including inside functions and ``try`` blocks, because each of
them can run.
"""

from __future__ import annotations

import ast

from migration_agent.analysis.parser import ParsedModule


def resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str | None:
    """Absolute dotted name for ``from <.level><target> import ...`` written in ``module``.

    Returns None when the relative import climbs above the top-level package.
    """
    if level == 0:
        return target
    package = module.split(".") if is_package else module.split(".")[:-1]
    if level - 1 >= len(package):
        return None
    base = package[: len(package) - (level - 1)]
    if target:
        base.append(target)
    return ".".join(base) or None


def iter_imports(parsed: ParsedModule):
    """Yield ``(absolute_module, imported_names)`` for every import in a file.

    ``imported_names`` is empty for ``import x`` and holds the names (or ``*``)
    for ``from x import a, b``.
    """
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, []
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(parsed.module, parsed.is_package, node.level, node.module)
            if base is not None:
                yield base, [alias.name for alias in node.names]


class ImportGraphBuilder:
    def __init__(self) -> None:
        #: dotted module name -> repository path
        self.index: dict[str, str] = {}

    def _in_repo(self, dotted: str) -> str | None:
        """Path of the longest in-repo prefix of a dotted name, if any."""
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            path = self.index.get(".".join(parts[:end]))
            if path:
                return path
        return None

    def build(self, modules: dict[str, ParsedModule]) -> dict[str, list[str]]:
        """Map every file to the sorted in-repo files it depends on."""
        self.index = {parsed.module: path for path, parsed in modules.items()}
        graph: dict[str, list[str]] = {}
        for path, parsed in modules.items():
            deps: set[str] = set()
            for base, names in iter_imports(parsed):
                targets = [base]
                # "from pkg import sub" depends on the submodule when sub is one.
                targets += [f"{base}.{name}" for name in names if name != "*"]
                for target in targets:
                    dep = self._in_repo(target)
                    if dep and dep != path:
                        deps.add(dep)
            graph[path] = sorted(deps)
        return graph
