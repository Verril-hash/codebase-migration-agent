from sqlalchemy import func, select
from sqlalchemy.orm import Session

from store.models import Order


class OrderRepository:
    def __init__(self, engine):
        self.session = Session(engine)

    def add(self, total):
        self.session.add(Order(total=total))
        self.session.commit()

    def totals(self):
        return list(self.session.scalars(select(Order.total).order_by(Order.id)))

    def big_orders(self, threshold):
        def above(limit):
            query = select(func.count()).select_from(Order).where(Order.total > limit)
            return self.session.scalar(query)

        return above(threshold)
