"""Test thuật toán Bài 02. Không gọi GenAI, không cần database.

Chia 3 nhóm theo yêu cầu nộp bài: ca thường, ca sát giới hạn, dữ liệu sai.
"""

import random
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.docks.algo import (
    Booking,
    Dock,
    DockError,
    Interval,
    InvalidDurationError,
    InvalidIntervalError,
    NaiveDatetimeError,
    SlotRequest,
    first_gap,
    merge_intervals,
    suggest_slot,
)

VN = timezone(timedelta(hours=7))
DOCKS = [Dock("A", "standard"), Dock("B", "standard"), Dock("C", "refrigerated")]


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 21, hour, minute, tzinfo=VN)


def iv(h1: int, m1: int, h2: int, m2: int) -> Interval:
    return Interval(at(h1, m1), at(h2, m2))


def book(dock: str, h1: int, m1: int, h2: int, m2: int, request_id: str | None = None) -> Booking:
    return Booking(dock, at(h1, m1), at(h2, m2), request_id)


def sample_bookings() -> list[Booking]:
    """Lịch trong đề: A 09:00-10:00, 10:00-11:00; B 09:30-10:15."""
    return [book("A", 9, 0, 10, 0), book("A", 10, 0, 11, 0), book("B", 9, 30, 10, 15)]


def suggest(earliest: datetime, minutes: int, bookings=None, dock_type="standard", **kwargs):
    request = SlotRequest(earliest, minutes, dock_type)
    return suggest_slot(request, DOCKS, sample_bookings() if bookings is None else bookings, VN, **kwargs)


# --- Ca thường --------------------------------------------------------------

def test_touching_bookings_merge_into_one_busy_block():
    assert merge_intervals([iv(9, 0, 10, 0), iv(10, 0, 11, 0)]) == [iv(9, 0, 11, 0)]


def test_merge_overlapping_and_unsorted_input():
    merged = merge_intervals([iv(13, 0, 14, 0), iv(9, 0, 10, 30), iv(10, 0, 10, 15), iv(10, 15, 11, 0)])
    assert merged == [iv(9, 0, 11, 0), iv(13, 0, 14, 0)]


def test_sample_from_exercise_gives_dock_b_at_10_15():
    result = suggest(at(9, 0), 60)
    assert (result.status, result.dock_id, result.start, result.end) == ("ok", "B", at(10, 15), at(11, 15))
    assert "A: bận 09:00-11:00" in result.reason
    assert "Chọn B 10:15-11:15 (GMT+7)" in result.reason


def test_result_changes_after_r01_is_booked():
    bookings = sample_bookings() + [book("B", 10, 15, 11, 15, "R01")]
    result = suggest(at(9, 0), 60, bookings)
    # A trống từ 11:00, B giờ bận tới 11:15 -> chọn A
    assert (result.dock_id, result.start) == ("A", at(11, 0))


def test_empty_schedule_starts_at_earliest():
    result = suggest(at(9, 0), 60, bookings=[])
    assert (result.dock_id, result.start) == ("A", at(9, 0))


def test_refrigerated_request_only_uses_cold_dock():
    result = suggest(at(9, 0), 30, dock_type="refrigerated")
    assert result.dock_id == "C"
    assert [c.dock_id for c in result.checks] == ["C"]


# --- Ca sát giới hạn --------------------------------------------------------

def test_booking_ending_when_new_one_starts_is_not_a_conflict():
    # Lịch cũ kết thúc 10:00, xe mới vào 10:00: hợp lệ vì khoảng là [start, end)
    bookings = [book("A", 9, 0, 10, 0), book("B", 9, 0, 10, 0)]
    result = suggest(at(10, 0), 60, bookings)
    assert (result.dock_id, result.start) == ("A", at(10, 0))


def test_gap_exactly_as_long_as_duration_fits():
    busy = merge_intervals([iv(9, 0, 10, 0), iv(11, 0, 12, 0)])
    assert first_gap(busy, at(9, 0), timedelta(minutes=60), at(8, 0), at(18, 0)) == at(10, 0)


def test_gap_one_minute_short_is_skipped():
    busy = merge_intervals([iv(9, 0, 10, 0), iv(10, 59, 12, 0)])
    assert first_gap(busy, at(9, 0), timedelta(minutes=60), at(8, 0), at(18, 0)) == at(12, 0)


def test_slot_may_end_exactly_at_closing_time():
    result = suggest(at(17, 0), 60, bookings=[])
    assert (result.status, result.end) == ("ok", at(18, 0))


def test_slot_ending_after_closing_is_no_slot():
    assert suggest(at(17, 1), 60, bookings=[]).status == "no_slot"


def test_arriving_before_opening_waits_until_opening():
    result = suggest(at(7, 0), 60, bookings=[])
    assert result.start == at(8, 0)


def test_same_start_on_two_docks_picks_alphabetical():
    result = suggest(at(9, 0), 60, bookings=[book("B", 7, 0, 8, 0)])
    assert result.dock_id == "A"
    assert "cùng giờ với B" in result.reason


def test_other_timezone_is_compared_by_real_instant():
    # 02:00 UTC = 09:00 GMT+7 -> cùng kết quả như ví dụ trong đề
    result = suggest(datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc), 60)
    assert (result.dock_id, result.start) == ("B", at(10, 15))
    assert result.start.utcoffset() == timedelta(hours=7)  # trả về theo giờ kho


def test_cleanup_buffer_extends_busy_blocks():
    # Có 15 phút dọn cửa: A bận tới 11:15, B bận tới 10:30 -> B 10:30
    result = suggest(at(9, 0), 60, cleanup=timedelta(minutes=15))
    assert (result.dock_id, result.start) == ("B", at(10, 30))
    assert "15 phút dọn cửa" in result.reason


CLEANUP = timedelta(minutes=15)


def test_gap_must_fit_cleanup_before_next_booking():
    # A trống 11:15-12:20 (65 phút): đủ xe 60 phút nhưng không đủ thêm 15 phút dọn -> phải chờ tới 13:15
    bookings = [book("A", 9, 0, 11, 0), book("A", 12, 20, 13, 0), book("B", 8, 0, 18, 0)]
    result = suggest(at(11, 0), 60, bookings, cleanup=CLEANUP)
    assert (result.dock_id, result.start) == ("A", at(13, 15))


def test_gap_exactly_duration_plus_cleanup_is_ok():
    # A trống 11:15-12:30 = đúng 60 + 15 phút -> nhận được
    bookings = [book("A", 9, 0, 11, 0), book("A", 12, 30, 13, 0), book("B", 8, 0, 18, 0)]
    result = suggest(at(11, 0), 60, bookings, cleanup=CLEANUP)
    assert (result.dock_id, result.start, result.end) == ("A", at(11, 15), at(12, 15))


def test_last_truck_can_end_at_closing_time_with_cleanup():
    # Xe cuối ngày chỉ cần rời cửa đúng 18:00, dọn sau giờ đóng cửa
    bookings = [book("A", 8, 0, 16, 45), book("B", 8, 0, 18, 0)]
    result = suggest(at(8, 0), 60, bookings, cleanup=CLEANUP)
    assert (result.dock_id, result.start, result.end) == ("A", at(17, 0), at(18, 0))


def test_cleanup_suggestion_never_conflicts_with_confirm_rule():
    # Gợi ý luôn qua được luật kiểm tra lúc xác nhận (store.create_booking):
    # lịch cũ và lịch mới đều kéo dài thêm cleanup, không được chồng nhau
    rng = random.Random(15)
    for _ in range(300):
        bookings = []
        for dock in ("A", "B"):
            for _ in range(rng.randint(0, 6)):
                start = rng.randrange(8 * 60, 18 * 60 - 15, 5)
                end = min(start + rng.randrange(15, 120, 5), 18 * 60)
                bookings.append(Booking(dock, at(start // 60, start % 60), at(end // 60, end % 60)))
        minutes = rng.randrange(15, 120, 5)
        result = suggest(at(8, 0) + timedelta(minutes=rng.randrange(0, 600, 5)), minutes, bookings, cleanup=CLEANUP)
        if result.status != "ok":
            continue
        for b in bookings:
            if b.dock_id == result.dock_id:
                assert not (b.start < result.end + CLEANUP and b.end > result.start - CLEANUP)


def brute_force(bookings: list[Booking], earliest: datetime, minutes: int):
    """Cách chậm nhưng chắc chắn đúng: thử từng phút, từng cửa, để đối chiếu thuật toán."""
    start = max(earliest, at(8, 0))
    while start + timedelta(minutes=minutes) <= at(18, 0):
        end = start + timedelta(minutes=minutes)
        for dock in ["A", "B"]:  # thứ tự alphabet
            if all(b.end <= start or b.start >= end for b in bookings if b.dock_id == dock):
                return dock, start
        start += timedelta(minutes=1)
    return None, None


@pytest.mark.parametrize("seed", range(30))
def test_matches_brute_force_on_random_schedules(seed):
    rng = random.Random(seed)
    bookings = []
    for _ in range(rng.randint(0, 40)):
        start = at(8, 0) + timedelta(minutes=rng.randint(0, 570))
        bookings.append(Booking(rng.choice(["A", "B"]), start, start + timedelta(minutes=rng.randint(5, 90))))
    earliest = at(7, 30) + timedelta(minutes=rng.randint(0, 600))
    minutes = rng.randint(10, 120)

    result = suggest(earliest, minutes, bookings)
    assert (result.dock_id, result.start) == brute_force(bookings, earliest, minutes)


def test_10000_bookings_runs_fast():
    rng = random.Random(7)
    bookings = []
    for _ in range(10_000):
        start = at(8, 0) + timedelta(minutes=rng.randint(0, 590))
        bookings.append(Booking(rng.choice(["A", "B"]), start, start + timedelta(minutes=rng.randint(1, 30))))
    started = time.perf_counter()
    suggest(at(8, 0), 15, bookings)
    assert time.perf_counter() - started < 1.0  # O(n log n): khoảng vài chục ms


# --- Dữ liệu sai ------------------------------------------------------------

def test_end_before_start_is_rejected():
    with pytest.raises(InvalidIntervalError):
        Interval(at(10, 0), at(9, 0))


def test_zero_length_interval_is_rejected():
    with pytest.raises(InvalidIntervalError):
        Interval(at(10, 0), at(10, 0))


def test_naive_datetime_is_rejected():
    with pytest.raises(NaiveDatetimeError):
        SlotRequest(datetime(2026, 10, 21, 9, 0), 60, "standard")


@pytest.mark.parametrize("minutes", [0, -30, None, 1.5, True])
def test_invalid_duration_is_rejected(minutes):
    with pytest.raises(InvalidDurationError):
        SlotRequest(at(9, 0), minutes, "standard")


def test_unknown_dock_type_is_rejected():
    with pytest.raises(DockError):
        SlotRequest(at(9, 0), 60, "frozen")


def test_fully_booked_day_is_no_slot():
    bookings = [book("A", 8, 0, 18, 0), book("B", 8, 0, 17, 30)]
    result = suggest(at(8, 0), 60, bookings)
    assert (result.status, result.dock_id) == ("no_slot", None)
    assert "Không cửa standard nào trống" in result.reason


def test_arriving_after_closing_is_no_slot():
    result = suggest(at(18, 30), 30, bookings=[])
    assert result.status == "no_slot"
    assert "sau giờ đóng cửa" in result.reason


def test_duration_longer_than_working_day_is_no_slot():
    assert suggest(at(8, 0), 11 * 60, bookings=[]).status == "no_slot"


def test_no_dock_of_requested_type_is_no_slot():
    request = SlotRequest(at(9, 0), 30, "refrigerated")
    result = suggest_slot(request, [Dock("A", "standard")], [], VN)
    assert result.status == "no_slot"
    assert "không có cửa loại refrigerated" in result.reason
