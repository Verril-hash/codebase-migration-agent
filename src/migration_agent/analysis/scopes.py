"""Walk every scope of a module and qualify every call in it.

Owner: Verril (Module 2). Shared by the call-site matcher (2.4) and the call
graph builder (2.5), so both see calls the same way.

For each module the walker visits the module body, each class body and each
function, and for every call works out the fully qualified name of what is
called. Names come from the symbol table; variables get a type from a small,
flow-insensitive inference:

* ``x = Ctor(...)``, ``x = factory()``, ``x = factory()()``, using :mod:`knowledge`;
* ``with Ctor(...) as x``;
* parameter and variable annotations (``def f(session: Session)``);
* ``self.x = ...`` in any method gives ``self.x`` a type in every method;
* module-level variables are visible inside functions **and in other files**
  that import them (``from app.db import SessionLocal``), which is how real
  applications share an engine or a session factory.

Anything it cannot type is left unknown; it never guesses.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field

from migration_agent.analysis import knowledge
from migration_agent.analysis.parser import ParsedModule
from migration_agent.analysis.symbols import SymbolTable

FACTORY = "factory:"
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
#: Rounds of module-level inference, so a type can flow through a chain of imports.
GLOBAL_ROUNDS = 3


def dotted_name(expr: ast.expr) -> str | None:
    """``a.b.c`` for a chain of names and attributes, else None (``f().g``, ``x[0].g``)."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = dotted_name(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def _class_names(parsed: ParsedModule) -> Iterator[str]:
    """Qualified names of every class in a module, including nested ones."""

    def visit(body: list[ast.stmt], prefix: str) -> Iterator[str]:
        for node in body:
            if isinstance(node, ast.ClassDef):
                yield f"{prefix}.{node.name}"
                yield from visit(node.body, f"{prefix}.{node.name}")
            elif isinstance(node, FUNCTIONS):
                yield from visit(node.body, f"{prefix}.{node.name}")

    return visit(parsed.tree.body, parsed.module)


def walk_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Every node in ``node``'s own scope; nested defs are yielded but not entered.

    For a function, its decorators belong to the enclosing scope and are skipped.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(node, DEFINITIONS) and child in node.decorator_list:
            continue
        yield child
        if not isinstance(child, DEFINITIONS):
            yield from walk_scope(child)


@dataclass
class Scope:
    module: str
    #: Name reported as the enclosing function of calls made directly in this scope.
    qualname: str
    #: Prefix for names defined inside this scope.
    prefix: str
    parent: Scope | None = None
    types: dict[str, str] = field(default_factory=dict)
    #: For methods: types of ``self.<attr>`` gathered from the whole class.
    self_types: dict[str, str] | None = None
    #: For methods: qualified name of the enclosing class.
    class_name: str | None = None

    def lookup(self, name: str) -> str | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.types:
                return scope.types[name]
            scope = scope.parent
        return None


@dataclass
class CallInfo:
    """One call expression, with what the walker could work out about it."""

    node: ast.Call
    scope: Scope
    path: str
    #: Fully qualified callee, or None when it cannot be known statically.
    target: str | None


class ScopeWalker:
    def __init__(self, symbols: SymbolTable) -> None:
        self.symbols = symbols
        #: qualified function name -> file, for every function and method seen
        self.function_files: dict[str, str] = {}
        #: module -> {module-level variable -> type}
        self.module_types: dict[str, dict[str, str]] = {}
        #: qualified names of every class defined in the repository
        self.classes: set[str] = set()

    # ------------------------------------------------------------ public
    def calls(self, modules: dict[str, ParsedModule]) -> Iterator[CallInfo]:
        """Every call in every module, in a deterministic order."""
        self.classes = {name for parsed in modules.values() for name in _class_names(parsed)}
        self._infer_module_types(modules)
        for path in sorted(modules):
            parsed = modules[path]
            scope = self._module_scope(parsed)
            scope.types = dict(self.module_types.get(parsed.module, {}))
            self.function_files[scope.qualname] = path
            yield from self._visit_scope(parsed.tree, scope, path)

    # ------------------------------------------------------------ module-level types
    @staticmethod
    def _module_scope(parsed: ParsedModule) -> Scope:
        return Scope(parsed.module, f"{parsed.module}.<module>", parsed.module)

    def _infer_module_types(self, modules: dict[str, ParsedModule]) -> None:
        for _ in range(GLOBAL_ROUNDS):
            for path in sorted(modules):
                parsed = modules[path]
                scope = self._module_scope(parsed)
                self._collect_types(list(walk_scope(parsed.tree)), scope)
                self.module_types[parsed.module] = scope.types

    def _global_type(self, qualified: str) -> tuple[str | None, list[str]]:
        """Type of a module-level variable named by a qualified name, plus leftover parts."""
        parts = qualified.split(".")
        for end in range(len(parts) - 1, 0, -1):
            variables = self.module_types.get(".".join(parts[:end]))
            if variables and parts[end] in variables:
                return variables[parts[end]], parts[end + 1 :]
        return None, []

    # ------------------------------------------------------------ qualifying
    def qualify(self, expr: ast.expr, scope: Scope) -> str | None:
        """Fully qualified name of a callee expression, or None."""
        if isinstance(expr, ast.Attribute) and not isinstance(expr.value, ast.Name | ast.Attribute):
            receiver = self.infer(expr.value, scope)  # e.g. Session().query
            if receiver and not receiver.startswith(FACTORY):
                return knowledge.canonical(f"{receiver}.{expr.attr}")
            return None
        name = dotted_name(expr)
        if name is None:
            return None
        head, *rest = name.split(".")
        if head == "self" and rest and scope.class_name:
            attr_type = (scope.self_types or {}).get(rest[0])
            if attr_type:
                return knowledge.canonical(".".join([attr_type, *rest[1:]]))
            return f"{scope.class_name}.{'.'.join(rest)}"
        local = scope.lookup(head)
        if local is not None:
            return (
                None if local.startswith(FACTORY) else knowledge.canonical(".".join([local, *rest]))
            )
        resolved = self.symbols.resolve(name, scope.module)
        if resolved is None:
            return None
        var_type, leftover = self._global_type(resolved)
        if var_type is not None:
            if var_type.startswith(FACTORY):
                return None
            return knowledge.canonical(".".join([var_type, *leftover]))
        return knowledge.canonical(resolved)

    def infer(self, expr: ast.expr | None, scope: Scope) -> str | None:
        """Type of an expression, or ``factory:<type>`` for a factory, or None."""
        if expr is None:
            return None
        if isinstance(expr, ast.Name):
            local = scope.lookup(expr.id)
            if local is not None:
                return local
            resolved = self.symbols.resolve(expr.id, scope.module)
            var_type, leftover = self._global_type(resolved) if resolved else (None, [])
            return var_type if not leftover else None
        if isinstance(expr, ast.Attribute):
            if isinstance(expr.value, ast.Name) and expr.value.id == "self":
                return (scope.self_types or {}).get(expr.attr)
            name = dotted_name(expr)
            resolved = self.symbols.resolve(name, scope.module) if name else None
            var_type, leftover = self._global_type(resolved) if resolved else (None, [])
            return var_type if not leftover else None
        if not isinstance(expr, ast.Call):
            return None
        held = self.infer(expr.func, scope)
        if held is not None:
            if held.startswith(FACTORY):
                return held[len(FACTORY) :]
            if held in knowledge.SELF_CALLABLE:
                return held
        callee = self.qualify(expr.func, scope)
        if callee in knowledge.CONSTRUCTS:
            return knowledge.CONSTRUCTS[callee]
        if callee in knowledge.FACTORIES:
            return FACTORY + knowledge.FACTORIES[callee]
        if callee in self.classes:  # constructing one of the repository's own classes
            return callee
        return None

    def _annotation(self, annotation: ast.expr | None, scope: Scope) -> str | None:
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return None
        name = dotted_name(annotation) if annotation is not None else None
        resolved = self.symbols.resolve(name, scope.module) if name else None
        return knowledge.canonical(resolved) if resolved else None

    # ------------------------------------------------------------ type collection
    def _collect_types(self, nodes: list[ast.AST], scope: Scope) -> None:
        """Bind variable types in source order; two passes settle simple chains."""
        bindings = [n for n in nodes if isinstance(n, ast.Assign | ast.AnnAssign | ast.With)]
        bindings.sort(key=lambda n: (n.lineno, n.col_offset))
        for _ in range(2):
            for node in bindings:
                if isinstance(node, ast.Assign):
                    value_type = self.infer(node.value, scope)
                    for target in node.targets:
                        if isinstance(target, ast.Name) and value_type:
                            scope.types[target.id] = value_type
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    found = self.infer(node.value, scope) or self._annotation(
                        node.annotation, scope
                    )
                    if found:
                        scope.types[node.target.id] = found
                elif isinstance(node, ast.With):
                    for item in node.items:
                        item_type = self.infer(item.context_expr, scope)
                        if isinstance(item.optional_vars, ast.Name) and item_type:
                            scope.types[item.optional_vars.id] = item_type

    def _bind_parameters(self, func: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope) -> None:
        args = func.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            declared = self._annotation(arg.annotation, scope)
            if declared:
                scope.types[arg.arg] = declared

    def _self_types(self, cls: ast.ClassDef, outer: Scope) -> dict[str, str]:
        """Types of ``self.<attr>`` assigned anywhere in the class's methods."""
        found: dict[str, str] = {}
        for method in cls.body:
            if not isinstance(method, FUNCTIONS):
                continue
            scope = Scope(outer.module, "", "", parent=outer)
            self._bind_parameters(method, scope)
            nodes = list(walk_scope(method))
            self._collect_types(nodes, scope)
            for node in nodes:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        value_type = self.infer(node.value, scope)
                        if value_type:
                            found[target.attr] = value_type
        return found

    # ------------------------------------------------------------ traversal
    def _visit_scope(self, node: ast.AST, scope: Scope, path: str) -> Iterator[CallInfo]:
        """Calls made directly in ``node``'s scope, then the scopes nested in it."""
        nodes = list(walk_scope(node))
        self._collect_types(nodes, scope)
        for child in nodes:
            if isinstance(child, ast.Call):
                yield CallInfo(child, scope, path, self.qualify(child.func, scope))
            elif isinstance(child, FUNCTIONS):
                yield from self._visit_function(child, scope, path)
            elif isinstance(child, ast.ClassDef):
                yield from self._visit_class(child, scope, path)

    def _visit_function(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope, path: str
    ) -> Iterator[CallInfo]:
        for decorator in func.decorator_list:  # decorators run in the enclosing scope
            for sub in ast.walk(decorator):
                if isinstance(sub, ast.Call):
                    yield CallInfo(sub, scope, path, self.qualify(sub.func, scope))
        qualname = f"{scope.prefix}.{func.name}"
        self.function_files[qualname] = path
        is_method = scope.class_name is not None and scope.qualname == scope.class_name
        inner = Scope(
            scope.module,
            qualname,
            qualname,
            # A method does not see its class body's names; a nested function sees its parent.
            parent=scope.parent if is_method else scope,
            # A closure inside a method still sees ``self``.
            self_types=scope.self_types,
            class_name=scope.class_name,
        )
        self._bind_parameters(func, inner)
        yield from self._visit_scope(func, inner, path)

    def _visit_class(self, cls: ast.ClassDef, scope: Scope, path: str) -> Iterator[CallInfo]:
        for decorator in cls.decorator_list:
            for sub in ast.walk(decorator):
                if isinstance(sub, ast.Call):
                    yield CallInfo(sub, scope, path, self.qualify(sub.func, scope))
        class_name = f"{scope.prefix}.{cls.name}"
        body = Scope(scope.module, class_name, class_name, parent=scope)
        methods = Scope(
            scope.module,
            class_name,
            class_name,
            parent=scope,
            self_types=self._self_types(cls, scope),
            class_name=class_name,
        )
        for child in walk_scope(cls):
            if isinstance(child, ast.Call):
                yield CallInfo(child, body, path, self.qualify(child.func, body))
            elif isinstance(child, FUNCTIONS):
                yield from self._visit_function(child, methods, path)
            elif isinstance(child, ast.ClassDef):
                yield from self._visit_class(child, body, path)
