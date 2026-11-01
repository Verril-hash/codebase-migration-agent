from shop.compat import DBSession
from shop.models import Product


def cheapest(engine):
    session = DBSession(engine)
    product = session.query(Product).order_by(Product.price).first()
    session.close()
    return product.name
