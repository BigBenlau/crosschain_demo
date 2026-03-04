"""SQLAlchemy Declarative Base 定義。

本檔只負責提供 ORM 模型繼承基類。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
