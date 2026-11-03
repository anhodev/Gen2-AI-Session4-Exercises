"""Bài 03 - Schema (mẫu dữ liệu).
  1. ExpenseExtraction: mẫu JSON GenAI phải điền từ văn bản chứng từ (đã OCR).
  2. Request / Response của API.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import Issue

# Loại tiền app hỗ trợ và số chữ số lẻ của đơn vị nhỏ nhất:
# VND không có số lẻ (1 đồng), USD có 2 (1 cent = 0,01 USD).
MINOR_DIGITS = {"VND": 0, "USD": 2}

# Chỉ những ô này được phép ghi vào missing_fields
MissingField = Literal["vendor", "invoice_number", "transaction_date", "amount_minor", "currency", "receipt_id"]

CODE_MAX = 64  # độ dài tối đa của số hóa đơn, mã biên nhận


# 1. Mẫu GenAI phải điền
class ExpenseExtraction(BaseModel):
    """Thông tin GenAI đọc ra từ một chứng từ chi phí (hóa đơn, biên nhận)."""

    # extra="forbid": GenAI trả thêm ô lạ thì báo lỗi, không lặng lẽ bỏ qua.
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(..., description="Chép đúng mã chứng từ hệ thống đã cấp. Không tự tạo mã mới.")
    vendor: str | None = Field(
        ..., max_length=200, description="Tên nơi bán ghi trên chứng từ, giữ nguyên cách viết. null nếu không đọc được."
    )
    invoice_number: str | None = Field(
        ...,
        max_length=CODE_MAX,
        description=(
            "Số hóa đơn, chép đúng từng ký tự kể cả dấu gạch nối (INV-0042 khác INV0042). "
            "null nếu không đọc được."
        ),
    )
    transaction_date: date | None = Field(
        ..., description="Ngày giao dịch trên chứng từ, dạng YYYY-MM-DD. null nếu không đọc được."
    )
    amount_minor: int | None = Field(
        ...,
        strict=True,  # phải là số nguyên thật: 12.5 hay "1250000" đều bị từ chối
        ge=0,
        description=(
            "Tổng tiền ở đơn vị nhỏ nhất, là số nguyên không âm: VND tính bằng đồng (1.250.000đ -> 1250000), "
            "USD tính bằng cent (12,50 USD -> 1250). null nếu không đọc được."
        ),
    )
    currency: str | None = Field(
        ...,
        description=(
            "Mã loại tiền ISO 4217 gồm 3 chữ in hoa, ví dụ VND, USD, EUR. Ghi đúng loại tiền trên chứng từ, "
            "kể cả khi không phải VND hay USD. 'đ', '₫' là VND; '$' là USD. null nếu không đọc được."
        ),
    )
    receipt_id: str | None = Field(
        ...,
        max_length=CODE_MAX,
        description="Mã biên nhận (receipt), chép đúng từng ký tự. null nếu chứng từ không có hoặc không đọc được.",
    )
    missing_fields: list[MissingField] = Field(
        ...,
        description="Tên các ô để null vì không đọc được. Ví dụ ['invoice_number', 'receipt_id'].",
    )


# 2. API
class ExtractRequest(BaseModel):
    """POST /expenses/extract: văn bản chứng từ (đã OCR). Mã chứng từ do backend tự cấp."""

    text: str = Field(min_length=1, max_length=20000)


class ExtractResponse(BaseModel):
    run_id: int | None = None
    submission_id: str                    # mã backend vừa cấp
    llm_output: dict | None               # JSON GenAI trả về, giữ nguyên để người dùng xem
    extraction: ExpenseExtraction | None  # bản đã kiểm tra lớp 1, None nếu sai khuôn
    issues: list[Issue]
    can_check: bool                       # False nếu còn lỗi (vd tiền âm, loại tiền chưa hỗ trợ)
