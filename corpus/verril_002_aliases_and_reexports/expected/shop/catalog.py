from sqlalchemy import select

from shop.compat import DBSession
from shop.models import Product


def cheapest(engine):
    session = DBSession(engine)
    product = session.execute(select(Product).order_by(Product.price)).scalars().first()
    session.close()
    return product.name
