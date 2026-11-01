import pytest
from shop import catalog, inventory
from shop.models import Base, Product
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Product(name="lamp", price=40),
                Product(name="pen", price=2),
                Product(name="desk", price=150),
            ]
        )
        session.commit()
    return engine


def test_cheapest_product(engine):
    assert catalog.cheapest(engine) == "pen"


def test_product_count(engine):
    assert inventory.count(engine) == 3
