"""Tiện ích thời gian dùng chung: múi giờ của app, giờ mở cửa."""

from __future__ import annotations

from datetime import datetime, time, tzinfo
from zoneinfo import ZoneInfo


def get_tz(name: str) -> ZoneInfo:
    """
    Lấy múi giờ theo tên, ví dụ "Asia/Ho_Chi_Minh" (GMT+7).

    Args:
        name (str): Tên múi giờ IANA, lấy từ APP_TIMEZONE.

    Returns:
        ZoneInfo: Múi giờ.
    """
    return ZoneInfo(name)


def to_app_tz(value: datetime, tz: tzinfo) -> datetime:
    """
    Đưa thời gian về múi giờ của app.

    - Không có múi giờ (vd email chỉ viết "9 giờ"): coi là giờ địa phương, gắn tz.
    - Có múi giờ khác: đổi sang tz, vẫn là cùng một thời điểm.

    Args:
        value (datetime): Thời gian cần đổi.
        tz (tzinfo): Múi giờ của app.

    Returns:
        datetime: Thời gian có múi giờ tz.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def parse_hhmm(value: str) -> time:
    """
    Đọc giờ dạng "HH:MM", ví dụ "08:00".

    Raises:
        ValueError: Sai định dạng.
    """
    return datetime.strptime(value.strip(), "%H:%M").time()


def gmt_label(value: datetime) -> str:
    """
    Nhãn múi giờ ngắn gọn để hiển thị.

    Ví dụ: +07:00 -> "GMT+7", +05:30 -> "GMT+5:30", UTC -> "GMT+0".
    """
    offset = value.utcoffset()
    if offset is None:
        return ""
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours}" + (f":{mins:02d}" if mins else "")
