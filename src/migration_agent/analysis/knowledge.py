"""What the analyser needs to know about a library's types.

Owner: Verril (Module 2). A pattern such as ``sqlalchemy.orm.Session.query`` is
a method, so a call ``session.query(...)`` only matches once we know that the
variable ``session`` holds a ``Session``. These tables say which calls produce
which types. They are data, not logic: when spec files gain a section for
type knowledge (Module 1), these entries move there.

All names are canonical: the public import path the patterns are written with.
"""

from __future__ import annotations

ENGINE = "sqlalchemy.engine.Engine"
CONNECTION = "sqlalchemy.engine.Connection"
SESSION = "sqlalchemy.orm.Session"

#: Other import paths of the same object -> canonical name.
ALIASES: dict[str, str] = {
    "sqlalchemy.Engine": ENGINE,
    "sqlalchemy.engine.base.Engine": ENGINE,
    "sqlalchemy.Connection": CONNECTION,
    "sqlalchemy.engine.base.Connection": CONNECTION,
    "sqlalchemy.orm.session.Session": SESSION,
    "sqlalchemy.orm.session.sessionmaker": "sqlalchemy.orm.sessionmaker",
    "sqlalchemy.orm.scoping.scoped_session": "sqlalchemy.orm.scoped_session",
    "sqlalchemy.engine.create_engine": "sqlalchemy.create_engine",
    "sqlalchemy.engine.create.create_engine": "sqlalchemy.create_engine",
}

#: Calling this produces an object of that type.
CONSTRUCTS: dict[str, str] = {
    "sqlalchemy.create_engine": ENGINE,
    f"{ENGINE}.connect": CONNECTION,
    f"{ENGINE}.begin": CONNECTION,
    SESSION: SESSION,
    # scoped_session returns a registry that proxies Session methods.
    "sqlalchemy.orm.scoped_session": SESSION,
}

#: Calling this produces a factory; calling the factory produces that type.
FACTORIES: dict[str, str] = {
    "sqlalchemy.orm.sessionmaker": SESSION,
}

#: Types whose instances are themselves callable and return the same type
#: (a scoped_session registry: ``Session()`` returns the current session).
SELF_CALLABLE: frozenset[str] = frozenset({SESSION})

#: Every type the tables can infer; only these can be the receiver of a method pattern.
KNOWN_TYPES: frozenset[str] = frozenset({*CONSTRUCTS.values(), *FACTORIES.values()})


def canonical(name: str) -> str:
    """Replace the longest aliased prefix of a dotted name with its canonical form."""
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        prefix = ".".join(parts[:end])
        if prefix in ALIASES:
            return ".".join([ALIASES[prefix], *parts[end:]])
    return name
