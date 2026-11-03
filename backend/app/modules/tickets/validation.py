"""Bài 01 - Kiểm tra kết quả GenAI.

"JSON đúng khuôn vẫn có thể chứa thông tin sai" -> kiểm tra 2 lớp:
  Lớp 1 (Pydantic):  đúng kiểu, đủ ô, giá trị nằm trong danh sách cho phép, summary không rỗng.
  Lớp 2 (nghiệp vụ): ticket_id có đúng mã hệ thống cấp không, có cần người xem lại không...

Không raise exception: gom hết vấn đề vào một list để trang web hiển thị
một lần, người dùng sửa một lượt thay vì sửa từng lỗi.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .schemas import Issue, StaffOut, StaffUpdate, TicketExtraction

STAFF_ID = re.compile(r"^NV\d{2,}$")


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


def validate_staff(raw: Any) -> tuple[list[StaffOut] | None, list[Issue]]:
    """
    Kiểm tra danh sách nhân viên người dùng gửi lên (làm thêm). Người mới (staff_id=null) được cấp mã kế tiếp.

    Args:
        raw (Any): JSON dạng StaffUpdate.

    Returns:
        tuple[list[StaffOut] | None, list[Issue]]: Danh sách đã cấp mã (None nếu sai khuôn) và các vấn đề.
        Field dạng "staff.1.name" để giao diện hiện cạnh đúng dòng.
    """
    try:
        parsed = StaffUpdate.model_validate(raw)
    except ValidationError as exc:
        return None, [Issue(field=".".join(map(str, e["loc"])), message=e["msg"], level="error") for e in exc.errors()]

    issues: list[Issue] = []
    seen: dict[str, int] = {}
    for i, person in enumerate(parsed.staff):
        if not person.name.strip():
            issues.append(Issue(field=f"staff.{i}.name", message="Tên nhân viên đang rỗng", level="error"))
        if len(set(person.skills)) != len(person.skills):
            issues.append(Issue(field=f"staff.{i}.skills", message="Kỹ năng bị ghi lặp", level="error"))
        if not person.skills:
            issues.append(Issue(field=f"staff.{i}.skills", message=f"{person.name} chưa có kỹ năng nào nên sẽ không nhận ticket", level="warning"))
        if person.staff_id is None:
            continue
        if not STAFF_ID.match(person.staff_id):
            issues.append(Issue(field=f"staff.{i}.staff_id", message=f"Mã '{person.staff_id}' không phải mã hệ thống cấp (NV01, NV02…)", level="error"))
        elif person.staff_id in seen:
            issues.append(Issue(field=f"staff.{i}.staff_id", message=f"Mã {person.staff_id} bị trùng với dòng {seen[person.staff_id] + 1}", level="error"))
        else:
            seen[person.staff_id] = i

    # Cấp mã cho người mới: số lớn nhất đang có + 1
    next_number = max((int(s[2:]) for s in seen), default=0) + 1
    staff = []
    for person in parsed.staff:
        staff_id = person.staff_id
        if staff_id is None:
            staff_id = f"NV{next_number:02d}"
            next_number += 1
        staff.append(StaffOut(staff_id=staff_id, name=person.name.strip(), skills=person.skills, max_tickets=person.max_tickets))
    return staff, issues
