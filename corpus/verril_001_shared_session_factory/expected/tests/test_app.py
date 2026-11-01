from app import stats, users
from app.db import engine
from app.models import Base


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_users_are_listed_in_insertion_order():
    users.add_user("ada")
    users.add_user("alan")
    assert users.names() == ["ada", "alan"]


def test_user_count():
    users.add_user("grace")
    assert stats.user_count() == 1
