"""Registry of deterministic codemods, keyed by the name used in spec files.

Owner: Adithya (Module 5). Each migration family gets its own sub-package,
for example ``codemods/sqlalchemy/``.
"""

from migration_agent.editing.codemods.sqlalchemy import DeclarativeBaseImport

CODEMODS: dict[str, type] = {}
CODEMODS["sqla_declarative_base"] = DeclarativeBaseImport
