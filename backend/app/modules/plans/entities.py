"""Bài 04 - Bảng plan_orders: mỗi lần bấm "Tính thứ tự" lưu một dòng.

Một biên bản (một run) có thể tính nhiều lần: gặp vòng chờ nhau thì người dùng sửa quan hệ rồi tính lại.
Mỗi lần giữ nguyên danh sách việc, các quan hệ đã xác nhận và kết quả, để xem lại vì sao lần đó ra như vậy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.entities import now_utc


class PlanOrder(Base):
    __tablename__ = "plan_orders"
    # Hai request tính cùng lúc cho một biên bản có thể cùng đếm ra một số lần tính: database từ chối dòng thứ hai
    __table_args__ = (UniqueConstraint("run_id", "version", name="uq_plan_orders_run_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)  # lần tính thứ mấy của biên bản này: 1, 2…

    tasks: Mapped[list] = mapped_column(JSON)         # [{id, title, owner}] mã T01…
    dependencies: Mapped[list] = mapped_column(JSON)  # [{before, after, evidence}] đã bỏ dòng ghi lặp
    status: Mapped[str] = mapped_column(String(20), index=True)  # ordered | cycle_detected
    result: Mapped[dict] = mapped_column(JSON)        # thứ tự kèm lý do, hoặc vòng và các việc bị kẹt
    warnings: Mapped[list] = mapped_column(JSON)      # cảnh báo lúc tính (không chặn)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
