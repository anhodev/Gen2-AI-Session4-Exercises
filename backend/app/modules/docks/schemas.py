"""Bài 02 - Schema (mẫu dữ liệu).
  1. DockRequestExtraction: mẫu JSON GenAI phải điền từ email đặt lịch.
  2. Request / Response của API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.schemas import Issue

DockType = Literal["standard", "refrigerated"]
# Chỉ những ô này được phép ghi vào missing_fields
MissingField = Literal["carrier", "earliest_start", "duration_minutes"]


# 1. Mẫu GenAI phải điền
class DockRequestExtraction(BaseModel):
    """Thông tin GenAI đọc ra từ email đặt lịch giao hàng."""

    # extra="forbid": GenAI trả thêm ô lạ thì báo lỗi, không lặng lẽ bỏ qua.
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., description="Chép đúng mã yêu cầu hệ thống đã cấp. Không tự tạo mã mới.")
    carrier: str | None = Field(..., description="Tên nhà vận chuyển. null nếu email không ghi.")
    earliest_start: datetime | None = Field(
        ...,
        description=(
            "Giờ sớm nhất xe có thể đến, dạng ISO 8601, ví dụ 2026-10-21T09:00:00. "
            "Giờ trong email là giờ địa phương của kho. null nếu email không ghi giờ đến."
        ),
    )
    duration_minutes: int | None = Field(
        ...,
        gt=0,
        description=(
            "Số phút cần dùng cửa kho để dỡ hàng, phải > 0. "
            "null nếu email không nói. KHÔNG tự đoán từ loại hàng hay số lượng hàng."
        ),
    )
    dock_type: DockType = Field(
        ...,
        description="refrigerated nếu hàng cần giữ lạnh/đông lạnh, ngược lại standard.",
    )
    missing_fields: list[MissingField] = Field(
        ...,
        description="Tên các ô để null vì email không có thông tin. Ví dụ ['duration_minutes'].",
    )


# 2. API
class ExtractRequest(BaseModel):
    """POST /requests/extract: chỉ cần email. Mã yêu cầu do backend tự sinh."""

    email: str = Field(min_length=1, max_length=5000)
    # Ngày nhận email, để GenAI hiểu "sáng mai", "thứ Tư này"... Bỏ trống = lúc gọi API.
    received_at: AwareDatetime | None = None


class ExtractResponse(BaseModel):
    run_id: int | None = None
    request_id: str
    llm_output: dict | None                   # JSON GenAI trả về, giữ nguyên để người dùng xem
    extraction: DockRequestExtraction | None  # bản đã kiểm tra, earliest_start đã gắn múi giờ kho
    issues: list[Issue]
    can_suggest: bool                         # False nếu còn lỗi (vd thiếu thời lượng)


class SuggestRequest(BaseModel):
    """POST /slots/suggest: bản người dùng đã xem/sửa."""

    run_id: int
    request: DockRequestExtraction


class BusyBlock(BaseModel):
    start: datetime
    end: datetime


class DockCheckOut(BaseModel):
    dock_id: str
    busy: list[BusyBlock]        # khoảng bận đã gộp
    free_from: datetime | None   # giờ sớm nhất cửa này nhận được xe


class SlotOut(BaseModel):
    status: Literal["ok", "no_slot"]
    dock_id: str | None
    start: datetime | None
    end: datetime | None
    reason: str
    checks: list[DockCheckOut]


class ConfirmRequest(BaseModel):
    """POST /bookings/confirm: đặt đúng cửa và giờ đã gợi ý. Backend kiểm tra lại trước khi lưu."""

    run_id: int
    request: DockRequestExtraction
    dock_id: str = Field(min_length=1, max_length=20)
    start: AwareDatetime


class BookingOut(BaseModel):
    booking_id: int
    request_id: str
    dock_id: str
    carrier: str | None
    start: datetime
    end: datetime
