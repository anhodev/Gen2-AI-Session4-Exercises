"""Bài 03 - Kiểm tra kết quả GenAI.

  Lớp 1 (Pydantic):  đúng kiểu, đủ ô, ngày đúng dạng YYYY-MM-DD, amount_minor là số nguyên >= 0,
                     số hóa đơn / mã biên nhận không quá dài, missing_fields trong danh sách cho phép.
  Lớp 2 (nghiệp vụ): submission_id đúng mã đã cấp, loại tiền được hỗ trợ, mã có chữ hoặc số,
                     ngày không ở tương lai, missing_fields khớp với các ô null,
                     thiếu mã thì báo trước là sẽ cần người xem lại.

Error chặn bước tìm trùng: app không được trả "chưa trùng" khi dữ liệu chưa đủ tin cậy.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from app.core.schemas import Issue

from .schemas import MINOR_DIGITS, ExpenseExtraction

# Các ô có thể không đọc được trên chứng từ
OPTIONAL_FIELDS = ("vendor", "invoice_number", "transaction_date", "amount_minor", "currency", "receipt_id")
TEXT_FIELDS = ("vendor", "invoice_number", "currency", "receipt_id")
CODE_FIELDS = ("invoice_number", "receipt_id")

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
OLD_AFTER = timedelta(days=365)  # chứng từ cũ hơn 1 năm thì nhắc kiểm tra lại


def validate_extraction(raw: Any, expected_submission_id: str, today: date) -> tuple[ExpenseExtraction | None, list[Issue]]:
    """
    Kiểm tra JSON GenAI trả về (hoặc bản người dùng đã sửa).

    Args:
        raw (Any): JSON thô.
        expected_submission_id (str): Mã chứng từ backend đã cấp.
        today (date): Ngày hôm nay theo múi giờ app, để bắt ngày giao dịch ở tương lai.

    Returns:
        tuple[ExpenseExtraction | None, list[Issue]]: Bản đã parse (None nếu sai khuôn)
        và danh sách vấn đề. Có lỗi "error" thì chưa được tìm trùng.
    """
    if not isinstance(raw, dict):
        return None, [Issue(field="(toàn bộ)", message="GenAI không trả về JSON object", level="error")]

    # Lớp 1: khuôn dữ liệu
    try:
        parsed = ExpenseExtraction.model_validate(raw)
    except ValidationError as exc:
        return None, [
            Issue(field=".".join(map(str, e["loc"])), message=e["msg"], level="error")
            for e in exc.errors()
        ]

    # Lớp 2: nghiệp vụ
    issues: list[Issue] = []

    if parsed.submission_id != expected_submission_id:
        issues.append(Issue(
            field="submission_id",
            message=f"GenAI trả '{parsed.submission_id}' nhưng hệ thống đã cấp '{expected_submission_id}'",
            level="error",
        ))

    # Chuỗi rỗng không phải "không đọc được": phải để null, nếu không sẽ tạo ra nhãn tra cứu sai
    for name in TEXT_FIELDS:
        value = getattr(parsed, name)
        if value is not None and not value.strip():
            issues.append(Issue(field=name, message=f"{name} đang là chuỗi rỗng. Không đọc được thì để null.", level="error"))

    # Số hóa đơn, mã biên nhận phải có ít nhất một chữ hoặc số (vd "---" là OCR đọc nhầm)
    for name in CODE_FIELDS:
        value = getattr(parsed, name)
        if value and value.strip() and not any(ch.isalnum() for ch in value):
            issues.append(Issue(field=name, message=f"{name} '{value}' không có chữ hoặc số nào", level="error"))

    issues += check_currency(parsed.currency)
    issues += check_amount(parsed.amount_minor)
    issues += check_date(parsed.transaction_date, today)
    issues += check_missing_fields(parsed)

    # Báo trước khi không đủ thông tin để tạo nhãn tra cứu (thuật toán sẽ trả needs_review)
    has_receipt = bool(parsed.receipt_id and parsed.receipt_id.strip())
    has_invoice = bool(parsed.vendor and parsed.vendor.strip() and parsed.invoice_number and parsed.invoice_number.strip())
    if not has_receipt and not has_invoice:
        issues.append(Issue(
            field="receipt_id",
            message="Thiếu mã biên nhận, và thiếu nơi bán hoặc số hóa đơn: không đủ để tìm trùng, chứng từ sẽ cần người xem lại",
            level="warning",
        ))

    return parsed, issues


def check_currency(currency: str | None) -> list[Issue]:
    """Loại tiền phải là mã 3 chữ in hoa và nằm trong danh sách hỗ trợ."""
    supported = ", ".join(MINOR_DIGITS)
    if currency is None:
        return [Issue(
            field="currency",
            message="Không đọc được loại tiền: không tạo được nhãn tra cứu, chứng từ sẽ cần người xem lại",
            level="warning",
        )]
    if not currency.strip():
        return []  # đã báo lỗi chuỗi rỗng ở trên
    if not CURRENCY_PATTERN.match(currency):
        return [Issue(field="currency", message=f"'{currency}' không phải mã loại tiền 3 chữ in hoa (vd {supported})", level="error")]
    if currency not in MINOR_DIGITS:
        return [Issue(field="currency", message=f"Loại tiền {currency} chưa được hỗ trợ. Chỉ nhận {supported}.", level="error")]
    return []


def check_amount(amount_minor: int | None) -> list[Issue]:
    """Số tiền âm đã bị lớp 1 chặn. Ở đây chỉ nhắc khi thiếu hoặc bằng 0."""
    if amount_minor is None:
        return [Issue(
            field="amount_minor",
            message="Không đọc được số tiền: vẫn tìm trùng theo mã, nhưng không so được chênh lệch",
            level="warning",
        )]
    if amount_minor == 0:
        return [Issue(field="amount_minor", message="Số tiền bằng 0, hãy kiểm tra lại chứng từ", level="warning")]
    return []


def check_date(transaction_date: date | None, today: date) -> list[Issue]:
    """Ngày giao dịch không được ở tương lai; quá cũ thì nhắc kiểm tra."""
    if transaction_date is None:
        return [Issue(field="transaction_date", message="Không đọc được ngày giao dịch", level="warning")]
    if transaction_date > today:
        return [Issue(
            field="transaction_date",
            message=f"Ngày giao dịch {transaction_date:%d/%m/%Y} sau hôm nay ({today:%d/%m/%Y}). Có thể OCR đọc sai.",
            level="error",
        )]
    if today - transaction_date > OLD_AFTER:
        return [Issue(field="transaction_date", message=f"Chứng từ ngày {transaction_date:%d/%m/%Y} đã hơn 1 năm", level="warning")]
    return []


def check_missing_fields(parsed: ExpenseExtraction) -> list[Issue]:
    """Ô null phải được ghi trong missing_fields, và ngược lại."""
    issues = []
    for name in OPTIONAL_FIELDS:
        is_null = getattr(parsed, name) is None
        if is_null and name not in parsed.missing_fields:
            issues.append(Issue(field="missing_fields", message=f"{name} đang null nhưng chưa ghi trong missing_fields", level="warning"))
        if not is_null and name in parsed.missing_fields:
            issues.append(Issue(field="missing_fields", message=f"missing_fields ghi {name} nhưng ô này đã có giá trị", level="warning"))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    """True nếu có ít nhất một Issue level="error"."""
    return any(i.level == "error" for i in issues)
