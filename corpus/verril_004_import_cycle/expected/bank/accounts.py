from sqlalchemy import select
from sqlalchemy.orm import Session

from bank import audit
from bank.models import Account


def open_account(engine, name):
    with Session(engine) as session:
        session.add(Account(name=name))
        session.commit()
    audit.record(engine, f"opened {name}")


def account_names(engine):
    with Session(engine) as session:
        return list(session.scalars(select(Account.name).order_by(Account.id)))
