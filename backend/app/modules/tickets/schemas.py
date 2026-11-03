"""Bài 01 - Schema (mẫu dữ liệu).
  1. TicketExtraction: mẫu JSON GenAI phải điền. Truyền vào
     with_structured_output.
  2. Request / Response của API
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import Issue  # dùng chung cho 4 bài

# Lấy danh sách cho phép từ một chỗ duy nhất, tránh mỗi file ghi một kiểu.
Intent = Literal["billing", "technical", "account", "other"]
Severity = Literal["low", "medium", "high", "critical"]

SUMMARY_MAX = 200


# 1. Mẫu GenAI phải điền
class TicketExtraction(BaseModel):
    """Thông tin GenAI đọc ra từ tin nhắn của khách hàng."""

    # extra="forbid": GenAI trả thêm ô lạ thì báo lỗi, không lặng lẽ bỏ qua.
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(..., description="Lấy mã ticket hệ thống đã cấp. Không tự tạo mã mới.")
    intent: Intent = Field(
        ...,
        description=(
            "Khách hỏi về việc gì: billing (thanh toán, bị trừ tiền), "
            "technical (lỗi kỹ thuật, app không chạy), account (đăng nhập, mật khẩu, tài khoản), "
            "other (việc khác hoặc tin nhắn mơ hồ)."
        ),
    )
    severity: Severity = Field(
        ...,
        description=(
            "Mức độ khẩn cấp: critical (mất tiền hoặc không dùng được dịch vụ chính, nhiều người bị ảnh hưởng), "
            "high (lỗi nghiêm trọng với một khách, ví dụ bị tính tiền hai lần), "
            "medium (lỗi có cách tạm khắc phục), low (câu hỏi, hướng dẫn sử dụng)."
        ),
    )
    product: str | None = Field(
        ..., description="Sản phẩm hoặc tính năng khách nhắc tới, ví dụ 'checkout'. null nếu khách không nói."
    )
    summary: str = Field(
        ..., max_length=SUMMARY_MAX, description=f"Tóm tắt vấn đề bằng tiếng Việt, tối đa {SUMMARY_MAX} ký tự."
    )
    needs_human_review: bool = Field(
        ..., description="true nếu tin nhắn mơ hồ hoặc không chắc chắn. Khi đó intent nên là 'other'."
    )

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError("Tóm tắt đang rỗng")
        return v


# 2. API
class ExtractRequest(BaseModel):
    """POST /tickets/extract: giờ nhận và tin nhắn. Mã ticket do backend tự cấp."""

    # AwareDatetime: Pydantic tự từ chối thời gian không có múi giờ.
    received_at: AwareDatetime
    message: str = Field(min_length=1, max_length=5000)


class ExtractResponse(BaseModel):
    run_id: int | None = None  # mã lần xử lý, gửi lại ở bước confirm
    ticket_id: str            # mã backend vừa cấp
    received_at: datetime
    llm_output: dict | None   # JSON GenAI trả về, giữ nguyên để người dùng xem
    issues: list[Issue]       # lỗi và cảnh báo sau khi code kiểm tra
    can_confirm: bool         # False nếu còn lỗi mức error


class ConfirmRequest(BaseModel):
    """POST /tickets/confirm: bản người dùng đã xem và sửa.

    Không nhận received_at từ trình duyệt: mã ticket và giờ nhận là dữ liệu
    hệ thống, lấy lại từ lần extract (run_id) để người dùng không sửa được.
    """

    run_id: int
    ticket: TicketExtraction


class ConfirmResponse(BaseModel):
    ticket_id: str
    severity: Severity
    received_at: datetime
    deadline_at: datetime
    sla_minutes: int
    edited_fields: list[str]  # các ô người dùng đã sửa so với GenAI
    warnings: list[Issue]


class QueueItem(BaseModel):
    position: int
    ticket_id: str
    severity: Severity
    intent: Intent
    product: str | None
    summary: str
    received_at: datetime
    deadline_at: datetime
    reason: str


class QueueResponse(BaseModel):
    total: int               # tổng số ticket đang chờ
    items: list[QueueItem]   # top K, xem không làm thay đổi hàng đợi
