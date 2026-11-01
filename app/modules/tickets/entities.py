"""Bài 01 - Bảng tickets: các ticket đã được nhân viên xác nhận."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.entities import now_utc


class Ticket(Base):
    __tablename__ = "tickets"

    # Mã hệ thống cấp làm khóa chính -> database tự chặn mã trùng
    ticket_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)

    intent: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    product: Mapped[str | None] = mapped_column(String(100), nullable=True)
    summary: Mapped[str] = mapped_column(String(200))
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # code tính, lưu lại để xem lịch sử

    # queued: đang trong hàng đợi; done: nhân viên đã lấy ra xử lý
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    popped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
