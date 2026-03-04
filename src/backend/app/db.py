"""資料庫連線與初始化工具。

本檔負責：
- 建立 SQLAlchemy engine 與 session factory
- 提供 `init_db()` 建表初始化
- 提供 `get_db()` 作為 API 層資料庫依賴
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base


is_sqlite = settings.db_path.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(settings.db_path, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
