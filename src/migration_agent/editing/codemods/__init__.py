"""Registry of deterministic codemods, keyed by the name used in spec files.

Owner: Adithya (Module 5). Each migration family gets its own sub-package,
for example ``codemods/sqlalchemy/``.
"""

CODEMODS: dict[str, type] = {}
