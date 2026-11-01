"""Bài 01 - Schema (mẫu dữ liệu).
  1. TicketExtraction: mẫu JSON GenAI phải điền. Truyền vào
     with_structured_output.
  2. Request / Response của API
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

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
class Issue(BaseModel):
    """Một vấn đề trong dữ liệu, hiển thị cạnh ô tương ứng trên trang web."""

    field: str
    message: str
    level: Literal["error", "warning"]  # error: chặn xác nhận; warning: chỉ nhắc


class ExtractRequest(BaseModel):
    """POST /tickets/extract: hệ thống đã biết mã và giờ nhận, GenAI đọc phần còn lại."""

    ticket_id: str = Field(min_length=1, max_length=50)
    # AwareDatetime: Pydantic tự từ chối thời gian không có múi giờ.
    received_at: AwareDatetime
    message: str = Field(min_length=1, max_length=5000)


class ExtractResponse(BaseModel):
    ticket_id: str
    received_at: datetime
    llm_output: dict | None   # JSON GenAI trả về, giữ nguyên để người dùng xem
    issues: list[Issue]       # lỗi và cảnh báo sau khi code kiểm tra
    can_confirm: bool         # False nếu còn lỗi mức error


class ConfirmRequest(BaseModel):
    """POST /tickets/confirm: bản người dùng đã xem và sửa."""

    received_at: AwareDatetime
    ticket: TicketExtraction


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
