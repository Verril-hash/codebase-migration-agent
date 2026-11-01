"""Resolve names to fully qualified names (sub-component 2.3).

Owner: Verril (Module 2). Design: Module 2 detailed design, class SymbolTable
and the resolve() pseudocode in section 6.2.

A deprecated API reaches a file under many spellings::

    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.ext import declarative as decl   ->  decl.declarative_base
    import sqlalchemy.ext.declarative                ->  the full dotted name
    from app.db import declarative_base              ->  re-exported by app/db/__init__.py

All of them resolve to ``sqlalchemy.ext.declarative.declarative_base``, the
single name a pattern is written against. Local variables (``session.query``
where ``session`` is a variable) need type inference and are handled later by
the matcher; for them :meth:`SymbolTable.resolve` returns None.
"""

from __future__ import annotations

import ast

from migration_agent.analysis.imports import resolve_relative
from migration_agent.analysis.parser import ParsedModule

#: Re-export chains longer than this are treated as unresolvable (guards cycles).
MAX_REEXPORT_DEPTH = 10


class SymbolTable:
    def __init__(self) -> None:
        #: module -> {local name -> qualified name}, from imports and top-level definitions
        self.bindings: dict[str, dict[str, str]] = {}
        #: module -> modules it star-imports from, in source order
        self.stars: dict[str, list[str]] = {}
        #: module -> names in its literal __all__, when it has one
        self.all_names: dict[str, list[str] | None] = {}

    # ------------------------------------------------------------ building
    @classmethod
    def build(cls, modules: dict[str, ParsedModule]) -> SymbolTable:
        table = cls()
        for parsed in modules.values():
            table._index_module(parsed)
        return table

    def _index_module(self, parsed: ParsedModule) -> None:
        module = parsed.module
        names: dict[str, str] = {}
        stars: list[str] = []

        for node in parsed.tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names[node.name] = f"{module}.{node.name}"
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names[target.id] = f"{module}.{target.id}"

        # Imports anywhere in the file; later statements override earlier ones.
        imports = [n for n in ast.walk(parsed.tree) if isinstance(n, ast.Import | ast.ImportFrom)]
        imports.sort(key=lambda n: (n.lineno, n.col_offset))
        for node in imports:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        names[alias.asname] = alias.name
                    else:
                        head = alias.name.split(".")[0]
                        names[head] = head
                continue
            base = resolve_relative(module, parsed.is_package, node.level, node.module)
            if base is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    stars.append(base)
                else:
                    names[alias.asname or alias.name] = f"{base}.{alias.name}"

        self.bindings[module] = names
        self.stars[module] = stars
        self.all_names[module] = parsed.all_names

    # ------------------------------------------------------------ resolving
    def exported(self, module: str) -> set[str] | None:
        """Names ``from module import *`` brings in; None for modules outside the repo."""
        if module not in self.bindings:
            return None
        explicit = self.all_names.get(module)
        if explicit is not None:
            return set(explicit)
        return {name for name in self.bindings[module] if not name.startswith("_")}

    def _lookup(self, head: str, module: str) -> str | None:
        bound = self.bindings.get(module, {}).get(head)
        if bound is not None:
            return bound
        for source in self.stars.get(module, []):
            exported = self.exported(source)
            if exported is not None and head in exported:
                return f"{source}.{head}"
        return None

    def _canonical(self, qualified: str) -> str | None:
        """Follow re-exports until the name points at where it is really defined."""
        for _ in range(MAX_REEXPORT_DEPTH):
            parts = qualified.split(".")
            for end in range(len(parts) - 1, 0, -1):
                owner = ".".join(parts[:end])
                if owner not in self.bindings:
                    continue
                target = self._lookup(parts[end], owner)
                if target is None or target == f"{owner}.{parts[end]}":
                    return qualified  # defined in the repo, or not known there
                qualified = ".".join([target, *parts[end + 1 :]])
                break
            else:
                return qualified  # owner is outside the repo: already canonical
        return None  # re-export cycle

    def resolve(self, name: str, module: str) -> str | None:
        """Qualified name of a dotted ``name`` as used in ``module``, or None if unknown."""
        head, *rest = name.split(".")
        bound = self._lookup(head, module)
        if bound is None:
            return None
        return self._canonical(".".join([bound, *rest]))

    def has_unresolved_star(self, module: str) -> bool:
        """True if the module star-imports from outside the repository (names unknowable)."""
        return any(self.exported(source) is None for source in self.stars.get(module, []))
