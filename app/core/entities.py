"""Bảng dùng chung cho cả 4 bài: lịch sử mỗi lần xử lý.

Đề yêu cầu lưu: nội dung gốc, JSON GenAI trả về, bản người dùng sửa,
kết quả cuối và thời gian xử lý để có thể xem lại.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def now_utc() -> datetime:
    """
    Lấy thời điểm hiện tại theo UTC.

    Returns:
        datetime: Thời điểm hiện tại, có múi giờ UTC.
    """
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(20), index=True)  # tickets, docks, expenses, plans
    # extracted: GenAI đã đọc, chờ người xác nhận
    # confirmed: người dùng đã xác nhận, có kết quả
    # rejected:  xác nhận bị từ chối (vd: trùng mã)
    status: Mapped[str] = mapped_column(String(20), default="extracted")

    raw_input: Mapped[dict] = mapped_column(JSON)                         # nội dung gốc người dùng gửi
    llm_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # JSON GenAI trả về
    user_output: Mapped[dict | None] = mapped_column(JSON, nullable=True) # bản người dùng đã sửa
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # kết quả cuối của thuật toán
    issues: Mapped[list | None] = mapped_column(JSON, nullable=True)      # lỗi/cảnh báo lúc kiểm tra
    error: Mapped[str | None] = mapped_column(Text, nullable=True)        # lỗi gọi model hoặc lý do từ chối

    model: Mapped[str] = mapped_column(String(50))   # tên model, hoặc "mock"
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # thời gian GenAI xử lý
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
