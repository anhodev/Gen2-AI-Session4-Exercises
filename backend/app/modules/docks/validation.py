"""Bài 02 - Kiểm tra kết quả GenAI.

  Lớp 1 (Pydantic):  đúng kiểu, đủ ô, duration > 0, dock_type và missing_fields trong danh sách cho phép.
  Lớp 2 (nghiệp vụ): request_id đúng mã đã cấp, thiếu thời lượng thì phải hỏi lại,
                     missing_fields khớp với các ô null, giờ đến so với giờ mở cửa.

Giờ không có múi giờ được hiểu theo múi giờ của kho (APP_TIMEZONE, mặc định GMT+7).
"""

from __future__ import annotations

from datetime import time, tzinfo
from typing import Any

from pydantic import ValidationError

from app.core.schemas import Issue
from app.core.timeutil import to_app_tz

from .algo import DockError, SlotRequest, working_window
from .schemas import DockRequestExtraction

# Các ô có thể bị thiếu trong email
OPTIONAL_FIELDS = ("carrier", "earliest_start", "duration_minutes")


def validate_extraction(
    raw: Any,
    expected_request_id: str,
    tz: tzinfo,
    open_time: time = time(8, 0),
    close_time: time = time(18, 0),
) -> tuple[DockRequestExtraction | None, list[Issue]]:
    """
    Kiểm tra JSON GenAI trả về (hoặc bản người dùng đã sửa).

    Args:
        raw (Any): JSON thô.
        expected_request_id (str): Mã yêu cầu backend đã sinh cho email này.
        tz (tzinfo): Múi giờ kho.
        open_time (time, optional): Giờ mở cửa. Defaults to 08:00.
        close_time (time, optional): Giờ đóng cửa. Defaults to 18:00.

    Returns:
        tuple[DockRequestExtraction | None, list[Issue]]: Bản đã parse (earliest_start đã gắn
        múi giờ kho, None nếu sai khuôn) và danh sách vấn đề. Có lỗi "error" thì chưa được tìm giờ.
    """
    if not isinstance(raw, dict):
        return None, [Issue(field="(toàn bộ)", message="GenAI không trả về JSON object", level="error")]

    # Lớp 1: khuôn dữ liệu
    try:
        parsed = DockRequestExtraction.model_validate(raw)
    except ValidationError as exc:
        return None, [
            Issue(field=".".join(map(str, e["loc"])), message=e["msg"], level="error")
            for e in exc.errors()
        ]

    # Gắn múi giờ kho nếu email không ghi múi giờ; đổi về giờ kho nếu khác
    if parsed.earliest_start is not None:
        parsed = parsed.model_copy(update={"earliest_start": to_app_tz(parsed.earliest_start, tz)})

    # Lớp 2: nghiệp vụ
    issues: list[Issue] = []

    if parsed.request_id != expected_request_id:
        issues.append(Issue(
            field="request_id",
            message=f"GenAI trả '{parsed.request_id}' nhưng hệ thống đã cấp '{expected_request_id}'",
            level="error",
        ))

    # Ô null phải được ghi trong missing_fields, và ngược lại
    for name in OPTIONAL_FIELDS:
        is_null = getattr(parsed, name) is None
        if is_null and name not in parsed.missing_fields:
            issues.append(Issue(field="missing_fields", message=f"{name} đang null nhưng chưa ghi trong missing_fields", level="warning"))
        if not is_null and name in parsed.missing_fields:
            issues.append(Issue(field="missing_fields", message=f"missing_fields ghi {name} nhưng ô này đã có giá trị", level="warning"))

    # Thiếu thông tin cần để tìm giờ: hỏi lại người dùng, không đoán
    if parsed.duration_minutes is None:
        issues.append(Issue(
            field="duration_minutes",
            message="Email không ghi cần bao lâu. Hãy hỏi lại nhà vận chuyển rồi nhập số phút (không đoán từ loại hàng).",
            level="error",
        ))
    if parsed.earliest_start is None:
        issues.append(Issue(field="earliest_start", message="Email không ghi giờ xe đến. Hãy nhập giờ sớm nhất xe có thể đến.", level="error"))
    if parsed.carrier is None:
        issues.append(Issue(field="carrier", message="Email không ghi nhà vận chuyển", level="warning"))

    # So với giờ mở cửa (chỉ cảnh báo, thuật toán sẽ xử lý)
    if parsed.earliest_start is not None:
        open_at, close_at = working_window(parsed.earliest_start, tz, open_time, close_time)
        if parsed.earliest_start < open_at:
            issues.append(Issue(field="earliest_start", message=f"Xe đến trước giờ mở cửa {open_time:%H:%M}, sẽ xếp từ {open_time:%H:%M}", level="warning"))
        elif parsed.earliest_start >= close_at:
            issues.append(Issue(field="earliest_start", message=f"Xe đến sau giờ đóng cửa {close_time:%H:%M}, trong ngày sẽ không còn chỗ", level="warning"))
        if parsed.duration_minutes and parsed.duration_minutes * 60 > (close_at - open_at).total_seconds():
            issues.append(Issue(field="duration_minutes", message="Thời lượng dài hơn cả ngày làm việc của kho", level="warning"))

    return parsed, issues


def has_errors(issues: list[Issue]) -> bool:
    """True nếu có ít nhất một Issue level="error"."""
    return any(i.level == "error" for i in issues)


def to_slot_request(parsed: DockRequestExtraction) -> SlotRequest:
    """
    Chuyển bản đã kiểm tra thành đầu vào của thuật toán.

    Raises:
        DockError: Còn thiếu giờ đến hoặc thời lượng (gọi validate_extraction trước để báo lỗi rõ hơn).
    """
    if parsed.earliest_start is None or parsed.duration_minutes is None:
        raise DockError("Còn thiếu giờ đến hoặc thời lượng, chưa tìm giờ được")
    return SlotRequest(parsed.earliest_start, parsed.duration_minutes, parsed.dock_type)
