import sqlalchemy.orm as orm

from shop.models import Product


def count(engine):
    with orm.Session(engine) as session:
        return session.query(Product).count()
