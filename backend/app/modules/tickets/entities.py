"""Bài 01 - Bảng ticket_numbers (cấp mã), tickets (ticket đã được nhân viên xác nhận)
và ticket_staff (nhân viên nhận ticket, phần làm thêm)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.entities import now_utc


class TicketNumber(Base):
    """Mã ticket do backend cấp cho mỗi tin nhắn: T01, T02..."""

    __tablename__ = "ticket_numbers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # số tự tăng, dùng để sinh mã
    ticket_id: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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


class TicketStaff(Base):
    """Nhân viên nhận ticket (làm thêm): kỹ năng là các loại yêu cầu xử lý được, kèm số việc tối đa."""

    __tablename__ = "ticket_staff"
    __table_args__ = (CheckConstraint("max_tickets >= 0", name="ck_ticket_staff_max"),)

    staff_id: Mapped[str] = mapped_column(String(20), primary_key=True)  # NV01, NV02... do backend cấp
    name: Mapped[str] = mapped_column(String(100))
    skills: Mapped[list] = mapped_column(JSON)       # vd ["billing", "account"]
    max_tickets: Mapped[int] = mapped_column(Integer)  # 0 = tạm không nhận
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
