"""Bài 03 - Bảng expense_submissions (chứng từ đã gửi) và expense_reviews (nhật ký duyệt)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.entities import now_utc


class ExpenseSubmission(Base):
    """Một chứng từ nhân viên gửi. Tạo lúc extract (để cấp mã), điền dữ liệu lúc kiểm tra trùng."""

    __tablename__ = "expense_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # số tự tăng, dùng để sinh mã E01, E02...
    submission_id: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)

    # Bản người dùng đã xem/sửa (null cho tới khi kiểm tra trùng)
    vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # đơn vị nhỏ nhất, số nguyên
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # new: mới extract, chưa kiểm tra; checked: đã kiểm tra trùng và nằm trong bảng tra cứu
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # Kết quả thuật toán: duplicate_candidate | needs_review | unique_under_current_rules
    check_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    matched_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)  # mã các chứng từ cũ trùng nhãn
    # Người duyệt: pending (chờ duyệt) | confirmed_duplicate | dismissed (bỏ nghi vấn). null nếu không cần duyệt.
    review_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExpenseReview(Base):
    """Nhật ký mỗi lần người duyệt thao tác: giữ bản trước và sau khi sửa để xem lại."""

    __tablename__ = "expense_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String(20), index=True)
    # confirm_duplicate | dismiss | recheck (sửa thông tin rồi kiểm tra lại)
    action: Mapped[str] = mapped_column(String(30))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # thông tin chứng từ trước khi sửa
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # sau khi sửa (giống before nếu không sửa)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # kết quả kiểm tra lại (nếu có)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)     # ghi chú của người duyệt
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
