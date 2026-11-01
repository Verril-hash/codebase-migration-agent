"""Find call sites that match deprecated patterns (sub-component 2.4).

Owner: Verril (Module 2). Design: Module 2 detailed design, class CallSiteMatcher
and figure 3 (call-site resolution flowchart).

A call matches when its qualified callee equals a pattern. A deprecated name
used without being called (``make = sessionmaker``, a callback list) matches
too: it has to be migrated just the same. A call that might be
deprecated but cannot be resolved is not dropped: it becomes an
:class:`UnresolvedSite` with a reason, so the verification loop can catch it:

* ``getattr(obj, "query")(...)`` -> dynamic attribute;
* ``eval("session.query(User)")`` -> dynamic call;
* ``s.query(...)`` where the type of ``s`` is unknown -> unknown receiver
  (only for method names some pattern uses);
* a pattern name reached through a star import of a third-party module.
"""

from __future__ import annotations

import ast

from migration_agent.analysis import knowledge
from migration_agent.analysis.parser import ParsedModule
from migration_agent.analysis.scopes import CALL, CallInfo, ScopeWalker
from migration_agent.analysis.symbols import SymbolTable
from migration_agent.models import CallSite, DeprecatedPattern, UnresolvedReason, UnresolvedSite


class CallSiteMatcher:
    def __init__(self, patterns: list[DeprecatedPattern], symbols: SymbolTable) -> None:
        self.symbols = symbols
        self.by_target = {knowledge.canonical(p.ast_matcher): p for p in patterns}
        #: final name of every pattern ("query", "declarative_base", ...)
        self.names = {target.rsplit(".", 1)[-1] for target in self.by_target}
        #: final names of patterns that are methods of a type we can infer
        self.method_names = {
            target.rsplit(".", 1)[1]
            for target in self.by_target
            if "." in target and target.rsplit(".", 1)[0] in knowledge.KNOWN_TYPES
        }
        self._unresolved: list[UnresolvedSite] = []

    def find(self, modules: dict[str, ParsedModule]) -> list[CallSite]:
        """Every call of a deprecated API, in file and line order."""
        self._unresolved = []
        sites: list[CallSite] = []
        for call in ScopeWalker(self.symbols).calls(modules):
            pattern = self.by_target.get(call.target) if call.target else None
            if pattern is not None:
                sites.append(
                    CallSite(
                        file=call.path,
                        line=call.node.lineno,
                        column=call.node.col_offset,
                        enclosing_function=call.scope.qualname,
                        pattern_id=pattern.pattern_id,
                    )
                )
                continue
            if call.kind != CALL:
                continue
            reason = self._unresolved_reason(call)
            if reason is not None:
                self._unresolved.append(
                    UnresolvedSite(call.path, call.node.lineno, ast.unparse(call.node), reason)
                )
        sites.sort(key=lambda s: (s.file, s.line, s.column))
        self._unresolved.sort(key=lambda u: (u.file, u.line))
        return sites

    def unresolved(self) -> list[UnresolvedSite]:
        """Sites from the last :meth:`find` that might be deprecated but cannot be resolved."""
        return list(self._unresolved)

    def _unresolved_reason(self, call: CallInfo) -> UnresolvedReason | None:
        func = call.node.func
        # getattr(obj, "query")(...)
        if (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and len(func.args) >= 2
            and isinstance(func.args[1], ast.Constant)
            and func.args[1].value in self.names
        ):
            return UnresolvedReason.DYNAMIC_ATTRIBUTE
        # eval("...query(...)") / exec(...)
        if isinstance(func, ast.Name) and func.id in {"eval", "exec"} and call.node.args:
            code = call.node.args[0]
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                if any(f"{name}(" in code.value for name in self.names):
                    return UnresolvedReason.DYNAMIC_CALL
            return None
        if call.target is not None:
            return None
        if isinstance(func, ast.Attribute) and func.attr in self.method_names:
            return UnresolvedReason.UNKNOWN_RECEIVER
        if (
            isinstance(func, ast.Name)
            and func.id in self.names
            and self.symbols.has_unresolved_star(call.scope.module)
        ):
            return UnresolvedReason.STAR_IMPORT
        return None
