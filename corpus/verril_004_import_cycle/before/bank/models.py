from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class AuditEntry(Base):
    __tablename__ = "audit"
    id = Column(Integer, primary_key=True)
    message = Column(String, nullable=False)
