from sqlalchemy import create_engine
from store.models import Base
from store.repository import OrderRepository


def make_repository():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = OrderRepository(engine)
    for total in (10, 250, 40, 900):
        repository.add(total)
    return repository


def test_totals_in_order():
    assert make_repository().totals() == [10, 250, 40, 900]


def test_big_orders():
    assert make_repository().big_orders(100) == 2
