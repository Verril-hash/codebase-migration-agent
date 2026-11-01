"""Build caller-to-callee edges between the repository's functions (sub-component 2.5).

Owner: Verril (Module 2). Design: Module 2 detailed design, class CallGraphBuilder.

Nodes are qualified names: ``app.svc.run``, ``app.repo.Repo.save``, and
``app.main.<module>`` for code at module level. An edge exists only between
functions defined in the repository; a call into a library is not an edge.
Constructing an in-repo class is an edge to its ``__init__``.
"""

from __future__ import annotations

from migration_agent.analysis.parser import ParsedModule
from migration_agent.analysis.scopes import CALL, ScopeWalker
from migration_agent.analysis.symbols import SymbolTable


class CallGraphBuilder:
    def __init__(self, symbols: SymbolTable) -> None:
        self.symbols = symbols
        #: qualified function name -> file, filled by :meth:`build`
        self.function_files: dict[str, str] = {}

    def build(self, modules: dict[str, ParsedModule]) -> dict[str, list[str]]:
        walker = ScopeWalker(self.symbols)
        calls = list(walker.calls(modules))
        self.function_files = dict(walker.function_files)
        defined = set(self.function_files)
        graph: dict[str, set[str]] = {name: set() for name in defined}
        for call in calls:
            callee = call.target
            if callee is None or call.kind != CALL:
                continue
            if callee in walker.classes:
                callee = f"{callee}.__init__"
            if callee in defined and callee != call.scope.qualname:
                graph.setdefault(call.scope.qualname, set()).add(callee)
        return {name: sorted(callees) for name, callees in sorted(graph.items())}
