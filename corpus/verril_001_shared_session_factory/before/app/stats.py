from app.db import engine


def user_count():
    return engine.execute("SELECT COUNT(*) FROM users").scalar()
