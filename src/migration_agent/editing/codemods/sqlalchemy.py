"""LibCST codemods for SQLAlchemy migrations."""

from __future__ import annotations

import libcst as cst


class DeclarativeBaseImport(cst.CSTTransformer):
    """Move SQLAlchemy's declarative module from ``ext`` to ``orm``."""

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        names = [
            alias.with_changes(name=cst.parse_expression("sqlalchemy.orm"))
            if cst.Module([]).code_for_node(alias.name) == "sqlalchemy.ext.declarative"
            else alias
            for alias in updated_node.names
        ]
        return updated_node.with_changes(names=names)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated_node.relative:
            return updated_node
        module = updated_node.module
        if (
            module is not None
            and cst.Module([]).code_for_node(module) == "sqlalchemy.ext.declarative"
        ):
            return updated_node.with_changes(module=cst.parse_expression("sqlalchemy.orm"))
        return updated_node
