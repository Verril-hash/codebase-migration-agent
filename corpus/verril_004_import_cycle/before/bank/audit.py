from sqlalchemy.orm import Session

from bank.models import AuditEntry


def record(engine, message):
    with Session(engine) as session:
        session.add(AuditEntry(message=message))
        session.commit()


def history(engine):
    from bank import accounts  # imported here: accounts imports this module

    with Session(engine) as session:
        entries = session.query(AuditEntry).order_by(AuditEntry.id).all()
        return [entry.message for entry in entries], accounts.account_names(engine)
