from sqlalchemy import text

from app.db import engine


def user_count():
    with engine.connect() as connection:
        return connection.execute(text("SELECT COUNT(*) FROM users")).scalar()
