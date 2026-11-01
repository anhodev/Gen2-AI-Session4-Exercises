"""Bài 01 - Kiểm tra kết quả GenAI.

"JSON đúng khuôn vẫn có thể chứa thông tin sai" -> kiểm tra 2 lớp:
  Lớp 1 (Pydantic):  đúng kiểu, đủ ô, giá trị nằm trong danh sách cho phép, summary không rỗng.
  Lớp 2 (nghiệp vụ): ticket_id có đúng mã hệ thống cấp không, có cần người xem lại không...

Không raise exception: gom hết vấn đề vào một list để trang web hiển thị
một lần, người dùng sửa một lượt thay vì sửa từng lỗi.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .schemas import Issue, TicketExtraction


def validate_extraction(raw: Any, expected_ticket_id: str) -> tuple[TicketExtraction | None, list[Issue]]:
    """
    Kiểm tra JSON GenAI trả về.

    Args:
        raw (Any): JSON thô GenAI trả về.
        expected_ticket_id (str): Mã ticket hệ thống đã cấp.

    Returns:
        tuple[TicketExtraction | None, list[Issue]]: Bản đã parse (None nếu sai khuôn)
        và danh sách vấn đề. Có Issue level="error" thì chưa được đưa ticket vào hàng đợi.
    """
    if not isinstance(raw, dict):
        return None, [Issue(field="(toàn bộ)", message="GenAI không trả về JSON object", level="error")]

    # Lớp 1: khuôn dữ liệu
    try:
        parsed = TicketExtraction.model_validate(raw)
    except ValidationError as exc:
        # Giữ nguyên thông báo của Pydantic, chỉ gắn vào đúng ô bị sai.
        return None, [
            Issue(field=".".join(map(str, e["loc"])), message=e["msg"], level="error")
            for e in exc.errors()
        ]

    # Lớp 2: nghiệp vụ
    issues: list[Issue] = []

    # GenAI phải chép đúng mã hệ thống đã cấp
    if parsed.ticket_id != expected_ticket_id:
        issues.append(Issue(
            field="ticket_id",
            message=f"GenAI trả '{parsed.ticket_id}' nhưng hệ thống đã cấp '{expected_ticket_id}'",
            level="error",
        ))

    if parsed.product is not None and not parsed.product.strip():
        issues.append(Issue(field="product", message="Để null thay vì chuỗi rỗng", level="warning"))

    # Cảnh báo, không chặn: người dùng quyết định sau khi đọc lại tin nhắn.
    if parsed.needs_human_review:
        issues.append(Issue(
            field="needs_human_review",
            message="GenAI không chắc chắn về tin nhắn này, hãy đọc lại trước khi xác nhận",
            level="warning",
        ))
    if parsed.intent == "other" and not parsed.needs_human_review:
        issues.append(Issue(
            field="intent",
            message="intent=other thường đi kèm needs_human_review=true",
            level="warning",
        ))

    return parsed, issues


def has_errors(issues: list[Issue]) -> bool:
    """
    Kiểm tra danh sách vấn đề có lỗi chặn xác nhận không.

    Args:
        issues (list[Issue]): Danh sách vấn đề.

    Returns:
        bool: True nếu có ít nhất một Issue level="error".
    """
    return any(i.level == "error" for i in issues)
