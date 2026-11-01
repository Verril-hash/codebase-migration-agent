from bank import accounts, audit
from bank.models import Base
from sqlalchemy import create_engine


def test_opening_an_account_is_audited():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    accounts.open_account(engine, "ada")
    accounts.open_account(engine, "alan")
    messages, names = audit.history(engine)
    assert messages == ["opened ada", "opened alan"]
    assert names == ["ada", "alan"]
