from sqlalchemy import Column, Integer, String

from shop.compat import make_base

Base = make_base()


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
