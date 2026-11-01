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
        return [account.name for account in session.query(Account).order_by(Account.id)]
