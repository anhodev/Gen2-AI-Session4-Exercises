"""Bài 02 - Bảng cửa kho, lịch đặt và mã yêu cầu."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DDL, CheckConstraint, DateTime, ForeignKey, String, event, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.entities import now_utc


class Dock(Base):
    """Một cửa nhận hàng của kho."""

    __tablename__ = "docks"

    dock_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    dock_type: Mapped[str] = mapped_column(String(20))  # standard | refrigerated

    __table_args__ = (CheckConstraint("dock_type in ('standard', 'refrigerated')", name="ck_docks_type"),)


class DockRequest(Base):
    """Mã yêu cầu do backend sinh cho mỗi email: R001, R002..."""

    __tablename__ = "dock_requests"

    id: Mapped[int] = mapped_column(primary_key=True)  # số tự tăng, dùng để sinh mã
    request_id: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | booked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DockBooking(Base):
    """Một lịch đã đặt: cửa nào, từ giờ nào đến giờ nào. Khoảng [start_at, end_at)."""

    __tablename__ = "dock_bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    dock_id: Mapped[str] = mapped_column(ForeignKey("docks.dock_id"), index=True)
    # Lịch có sẵn (seed) không có mã yêu cầu. Mỗi yêu cầu chỉ được đặt một lần.
    request_id: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_dock_bookings_end_after_start"),
        # Chỉ có trên PostgreSQL (Supabase): database tự từ chối 2 lịch chồng nhau trên cùng một cửa.
        # '[)' -> 10:00 kết thúc và 10:00 bắt đầu KHÔNG bị coi là chồng nhau.
        ExcludeConstraint(
            ("dock_id", "="),
            (text("tstzrange(start_at, end_at, '[)')"), "&&"),
            name="ex_dock_bookings_no_overlap",
            using="gist",
        ).ddl_if(dialect="postgresql"),
    )


# ExcludeConstraint cần extension btree_gist (để so sánh "=" trên cột chữ trong index gist)
event.listen(
    DockBooking.__table__,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS btree_gist").execute_if(dialect="postgresql"),
)
