import sqlalchemy.orm as orm
from sqlalchemy import func, select

from shop.models import Product


def count(engine):
    with orm.Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Product))
