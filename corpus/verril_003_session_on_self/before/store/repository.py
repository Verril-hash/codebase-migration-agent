from sqlalchemy.orm import Session

from store.models import Order


class OrderRepository:
    def __init__(self, engine):
        self.session = Session(engine)

    def add(self, total):
        self.session.add(Order(total=total))
        self.session.commit()

    def totals(self):
        return [order.total for order in self.session.query(Order).order_by(Order.id)]

    def big_orders(self, threshold):
        def above(limit):
            return self.session.query(Order).filter(Order.total > limit).count()

        return above(threshold)
