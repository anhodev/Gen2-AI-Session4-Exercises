"""Bài 02 - Thuật toán tìm giờ trống ở cửa kho.

  - Mỗi cửa chỉ phục vụ 1 xe tại một thời điểm. Khoảng thời gian dạng [start, end):
      xe rời cửa lúc 10:00 và xe sau vào lúc 10:00 là hợp lệ.
  - Với mỗi cửa đúng loại: sắp xếp lịch theo giờ bắt đầu, gộp các lịch chồng nhau
      hoặc nối sát nhau thành một khoảng bận. O(n log n).
  - Từ giờ xe có thể đến, tìm khoảng trống đầu tiên đủ dài trong giờ mở cửa.
  - Chọn giờ bắt đầu sớm nhất; cùng giờ thì chọn mã cửa đứng trước theo alphabet.
  - Không còn chỗ trong ngày -> no_slot. Không tự chuyển sang hôm sau.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo

from app.core.timeutil import gmt_label

DOCK_TYPES = ("standard", "refrigerated")


# Lỗi: mỗi loại một class để API trả thông báo rõ ràng
class DockError(ValueError):
    """Lỗi chung của module cửa kho."""


class NaiveDatetimeError(DockError):
    """Thời gian không có múi giờ."""


class InvalidIntervalError(DockError):
    """Giờ kết thúc không sau giờ bắt đầu."""


class InvalidDurationError(DockError):
    """Thời lượng không hợp lệ (thiếu, bằng 0 hoặc âm)."""


def require_aware(value: datetime, name: str) -> None:
    """Báo lỗi nếu thời gian thiếu múi giờ."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError(f"{name} '{value.isoformat()}' thiếu múi giờ")


@dataclass(frozen=True, order=True)
class Interval:
    """Khoảng thời gian [start, end). order=True: sắp xếp theo start rồi end."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        require_aware(self.start, "start")
        require_aware(self.end, "end")
        if self.end <= self.start:
            raise InvalidIntervalError(
                f"Giờ kết thúc {self.end.isoformat()} phải sau giờ bắt đầu {self.start.isoformat()}"
            )


@dataclass(frozen=True)
class Dock:
    dock_id: str
    dock_type: str  # standard | refrigerated


@dataclass(frozen=True)
class Booking:
    """Một lịch đã đặt ở một cửa."""

    dock_id: str
    start: datetime
    end: datetime
    request_id: str | None = None

    def interval(self) -> Interval:
        return Interval(self.start, self.end)


@dataclass(frozen=True)
class SlotRequest:
    """Yêu cầu đã được người dùng xác nhận (GenAI đọc, code kiểm tra)."""

    earliest_start: datetime
    duration_minutes: int
    dock_type: str

    def __post_init__(self) -> None:
        require_aware(self.earliest_start, "earliest_start")
        if isinstance(self.duration_minutes, bool) or not isinstance(self.duration_minutes, int):
            raise InvalidDurationError("duration_minutes phải là số nguyên (phút)")
        if self.duration_minutes <= 0:
            raise InvalidDurationError(f"duration_minutes phải > 0, nhận {self.duration_minutes}")
        if self.dock_type not in DOCK_TYPES:
            raise DockError(f"dock_type '{self.dock_type}' không hợp lệ. Chỉ nhận: {', '.join(DOCK_TYPES)}")

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_minutes)


@dataclass(frozen=True)
class DockCheck:
    """Kết quả kiểm tra một cửa, dùng để giải thích vì sao chọn/không chọn."""

    dock_id: str
    busy: list[Interval]          # các khoảng bận đã gộp
    free_from: datetime | None    # giờ sớm nhất cửa này nhận được xe, None nếu hết chỗ


@dataclass(frozen=True)
class SlotResult:
    status: str                   # "ok" hoặc "no_slot"
    dock_id: str | None
    start: datetime | None
    end: datetime | None
    reason: str
    checks: list[DockCheck] = field(default_factory=list)


def merge_intervals(intervals: list[Interval], cleanup: timedelta = timedelta(0)) -> list[Interval]:
    """
    Gộp các khoảng chồng nhau hoặc nối sát nhau. O(n log n) do sắp xếp.

    Ví dụ: 09:00-10:00 và 10:00-11:00 -> 09:00-11:00 (cửa bận liên tục).

    Args:
        intervals (list[Interval]): Các lịch đã có, không cần sắp xếp trước.
        cleanup (timedelta, optional): Thời gian dọn cửa sau mỗi xe, cộng vào giờ kết thúc.
            Defaults to 0.

    Returns:
        list[Interval]: Các khoảng bận, đã sắp xếp, không chồng nhau.
    """
    merged: list[Interval] = []
    for current in sorted(intervals):
        end = current.end + cleanup
        # start <= end của khoảng trước: chồng nhau hoặc nối sát -> gộp
        if merged and current.start <= merged[-1].end:
            if end > merged[-1].end:
                merged[-1] = Interval(merged[-1].start, end)
        else:
            merged.append(Interval(current.start, end))
    return merged


def first_gap(busy: list[Interval], earliest: datetime, duration: timedelta, open_at: datetime, close_at: datetime) -> datetime | None:
    """
    Tìm giờ bắt đầu sớm nhất còn trống đủ `duration`. O(n).

    Args:
        busy (list[Interval]): Khoảng bận đã gộp và sắp xếp (kết quả merge_intervals).
        earliest (datetime): Giờ sớm nhất xe có thể đến.
        duration (timedelta): Thời gian cần dùng cửa.
        open_at (datetime): Giờ mở cửa trong ngày.
        close_at (datetime): Giờ đóng cửa; xe phải rời cửa trước hoặc đúng giờ này.

    Returns:
        datetime | None: Giờ bắt đầu, None nếu không còn chỗ trong ngày.
    """
    cursor = max(earliest, open_at)  # đến trước giờ mở cửa thì chờ tới giờ mở cửa
    for interval in busy:
        if interval.end <= cursor:
            continue  # khoảng bận đã qua
        if interval.start - cursor >= duration:
            break  # khoảng trống trước khoảng bận này đủ dài
        cursor = max(cursor, interval.end)  # phải chờ hết khoảng bận
    return cursor if cursor + duration <= close_at else None


def working_window(day_of: datetime, tz: tzinfo, open_time: time, close_time: time) -> tuple[datetime, datetime]:
    """Giờ mở/đóng cửa trong ngày của `day_of`, tính theo múi giờ tz."""
    day = day_of.astimezone(tz).date()
    return (
        datetime.combine(day, open_time, tzinfo=tz),
        datetime.combine(day, close_time, tzinfo=tz),
    )


def suggest_slot(
    request: SlotRequest,
    docks: list[Dock],
    bookings: list[Booking],
    tz: tzinfo,
    open_time: time = time(8, 0),
    close_time: time = time(18, 0),
    cleanup: timedelta = timedelta(0),
) -> SlotResult:
    """
    Tìm cửa và giờ bắt đầu sớm nhất cho một xe.

    Args:
        request (SlotRequest): Giờ sớm nhất, thời lượng, loại cửa.
        docks (list[Dock]): Danh sách cửa của kho.
        bookings (list[Booking]): Lịch đã đặt (lấy từ dữ liệu mới nhất).
        tz (tzinfo): Múi giờ của kho (mặc định GMT+7).
        open_time (time, optional): Giờ mở cửa. Defaults to 08:00.
        close_time (time, optional): Giờ đóng cửa. Defaults to 18:00.
        cleanup (timedelta, optional): Thời gian dọn cửa giữa hai xe. Defaults to 0.

    Returns:
        SlotResult: status "ok" kèm cửa và giờ, hoặc "no_slot" kèm lý do.
    """
    earliest = request.earliest_start.astimezone(tz)
    open_at, close_at = working_window(earliest, tz, open_time, close_time)

    # Chỉ xét cửa đúng loại, sắp xếp theo mã để kết quả luôn giống nhau
    candidates = sorted((d for d in docks if d.dock_type == request.dock_type), key=lambda d: d.dock_id)
    if not candidates:
        return SlotResult("no_slot", None, None, None, f"Kho không có cửa loại {request.dock_type}")
    if earliest >= close_at:
        return SlotResult("no_slot", None, None, None, f"Xe đến lúc {fmt(earliest)}, sau giờ đóng cửa {fmt(close_at)}")

    # Nhóm lịch theo cửa
    by_dock: dict[str, list[Interval]] = defaultdict(list)
    for booking in bookings:
        by_dock[booking.dock_id].append(booking.interval())

    checks = []
    for dock in candidates:
        busy = merge_intervals(by_dock[dock.dock_id], cleanup)
        free_from = first_gap(busy, earliest, request.duration, open_at, close_at)
        checks.append(DockCheck(dock.dock_id, busy, free_from))

    # Giờ sớm nhất trước, cùng giờ thì mã cửa đứng trước theo alphabet
    available = [c for c in checks if c.free_from is not None]
    if not available:
        return SlotResult(
            "no_slot", None, None, None,
            f"Không cửa {request.dock_type} nào trống đủ {request.duration_minutes} phút "
            f"từ {fmt(earliest)} đến {fmt(close_at)}",
            checks,
        )
    best = min(available, key=lambda c: (c.free_from, c.dock_id))
    end = best.free_from + request.duration
    return SlotResult("ok", best.dock_id, best.free_from, end, explain(best, checks, request), checks)


def explain(best: DockCheck, checks: list[DockCheck], request: SlotRequest) -> str:
    """
    Giải thích vì sao chọn cửa và giờ này.

    Ví dụ: "Cửa A bận liên tục 09:00-11:00, trống sớm nhất 11:00; Cửa B trống sớm nhất 10:15.
    Chọn Cửa B 10:15-11:15 (GMT+7) vì sớm nhất."
    """
    parts = []
    for check in checks:
        busy = ", ".join(f"{fmt(i.start)}-{fmt(i.end)}" for i in check.busy) or "không có lịch"
        free = fmt(check.free_from) if check.free_from else "hết chỗ"
        parts.append(f"{check.dock_id}: bận {busy}; trống sớm nhất {free}")

    end = best.free_from + request.duration
    reason = f"Chọn {best.dock_id} {fmt(best.free_from)}-{fmt(end)} ({gmt_label(best.free_from)}) vì sớm nhất"
    same_time = [c.dock_id for c in checks if c.free_from == best.free_from and c.dock_id != best.dock_id]
    if same_time:
        reason += f"; cùng giờ với {', '.join(same_time)} nên chọn mã cửa đứng trước"
    return ". ".join(parts) + ". " + reason + "."


def fmt(value: datetime) -> str:
    return value.strftime("%H:%M")
