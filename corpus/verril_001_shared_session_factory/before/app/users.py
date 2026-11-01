from app.db import SessionLocal
from app.models import User


def add_user(name):
    session = SessionLocal()
    session.add(User(name=name))
    session.commit()
    session.close()


def names():
    session = SessionLocal()
    rows = session.query(User).order_by(User.id).all()
    session.close()
    return [row.name for row in rows]
