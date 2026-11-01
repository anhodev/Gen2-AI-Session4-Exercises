"""Kết nối database (SQLAlchemy), viết theo cấu trúc buổi 5.

Chạy thật: Supabase PostgreSQL. Chạy test: SQLite (file tạm).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(database_url: str):
    """
    Tạo engine và session factory.

    Args:
        database_url (str): postgresql+psycopg://... (Supabase) hoặc sqlite:///./data/app.db

    Returns:
        tuple[Engine, sessionmaker]: Engine và hàm tạo session.
    """
    connect_args = {}
    if database_url.startswith("sqlite"):
        # FastAPI chạy endpoint ở nhiều thread, SQLite mặc định chỉ cho 1 thread dùng.
        connect_args = {"check_same_thread": False}
        # Tạo thư mục chứa file .db nếu chưa có
        db_path = database_url.removeprefix("sqlite:///")
        if db_path and db_path != database_url:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    elif database_url.startswith("postgresql"):
        # Supabase pooler (transaction mode, cổng 6543) không hỗ trợ prepared statement.
        connect_args = {"prepare_threshold": None}

    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, session_local


def create_tables(engine: Engine) -> None:
    """Tạo các bảng còn thiếu. Gọi khi app khởi động."""
    # Import để SQLAlchemy biết có những bảng nào
    from app.core import entities  # noqa: F401
    from app.modules.tickets import entities as ticket_entities  # noqa: F401

    Base.metadata.create_all(bind=engine)


def to_utc(value: datetime) -> datetime:
    """
    Đổi thời gian về UTC trước khi lưu.

    PostgreSQL (timestamptz) lưu đúng thời điểm, nhưng SQLite bỏ mất múi giờ:
    lưu 09:00+07:00 sẽ thành 09:00 không rõ múi giờ. Đổi về UTC trước thì
    cả hai database đều lưu cùng một giá trị 02:00 UTC.

    Args:
        value (datetime): Thời gian có múi giờ.

    Raises:
        ValueError: Thời gian thiếu múi giờ.

    Returns:
        datetime: Cùng thời điểm, theo UTC.
    """
    if value.tzinfo is None:
        raise ValueError("Chỉ lưu thời gian có múi giờ")
    return value.astimezone(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """
    Đọc thời gian từ database, bảo đảm có múi giờ.

    Args:
        value (datetime | None): Giá trị đọc ra. SQLite trả về không có múi giờ.

    Returns:
        datetime | None: Thời gian có múi giờ UTC.
    """
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
