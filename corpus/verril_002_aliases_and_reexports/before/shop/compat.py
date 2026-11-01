"""The ORM pieces the package uses, under the names it uses them."""

from sqlalchemy.ext import declarative as decl
from sqlalchemy.orm import Session as DBSession  # noqa: F401  (re-exported)

make_base = decl.declarative_base
