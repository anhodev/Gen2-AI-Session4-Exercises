"""Test thuật toán Bài 01. Không gọi GenAI, không cần API key.

Chia 3 nhóm theo yêu cầu nộp bài: ca thường, ca sát giới hạn, dữ liệu sai.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.tickets.algo import (
    DuplicateTicketError,
    InvalidSeverityError,
    NaiveDatetimeError,
    TicketError,
    TicketQueue,
    make_ticket,
)

VN = timezone(timedelta(hours=7))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 21, hour, minute, tzinfo=VN)


def sample_queue() -> TicketQueue:
    """Ba ticket mẫu trong đề."""
    q = TicketQueue()
    q.add(make_ticket("T01", at(9, 0), "high"))
    q.add(make_ticket("T02", at(9, 20), "critical"))
    q.add(make_ticket("T03", at(8, 30), "medium"))
    return q


def ids(tickets) -> list[str]:
    return [t.ticket_id for t in tickets]


# --- Ca thường --------------------------------------------------------------

def test_deadline_is_computed_from_sla():
    assert make_ticket("T01", at(9, 0), "high").deadline_at == at(11, 0)
    assert make_ticket("T02", at(9, 20), "critical").deadline_at == at(9, 50)
    assert make_ticket("T03", at(8, 30), "medium").deadline_at == at(16, 30)
    assert make_ticket("T09", at(8, 0), "low").deadline_at == at(8, 0) + timedelta(days=1)


def test_sample_order_from_exercise():
    q = sample_queue()
    assert ids([q.pop_next(), q.pop_next(), q.pop_next()]) == ["T02", "T01", "T03"]
    assert q.pop_next() is None  # hết hàng đợi thì trả None, không lỗi


def test_top_k_does_not_remove_tickets():
    q = sample_queue()
    assert ids(q.peek_top(2)) == ["T02", "T01"]
    assert ids(q.peek_top(2)) == ["T02", "T01"]  # xem lần hai vẫn y nguyên
    assert len(q) == 3
    assert q.pop_next().ticket_id == "T02"  # lần lấy kế tiếp không bị ảnh hưởng


def test_new_ticket_with_earlier_deadline_jumps_to_front():
    q = sample_queue()
    q.add(make_ticket("T04", at(7, 0), "high"))  # hạn 09:00
    assert ids(q.peek_top(4)) == ["T04", "T02", "T01", "T03"]


def test_ranked_view_explains_reason():
    rows = sample_queue().ranked_view(2)
    assert [r["position"] for r in rows] == [1, 2]
    assert "hạn sớm hơn T01" in rows[0]["reason"]
    assert "hạn sớm hơn T03" in rows[1]["reason"]  # giải thích cả ticket cuối top K


# --- Ca sát giới hạn: các luật phá hòa -------------------------------------

def test_same_deadline_higher_severity_first():
    q = TicketQueue()
    q.add(make_ticket("A", at(10, 0), "high"))       # hạn 12:00
    q.add(make_ticket("B", at(11, 30), "critical"))  # hạn 12:00
    assert ids(q.peek_top(2)) == ["B", "A"]
    assert "khẩn cấp hơn" in q.ranked_view(1)[0]["reason"]


def test_same_severity_earlier_received_first():
    # Lưu ý: cùng severity thì cùng SLA, nên cùng hạn <=> cùng giờ nhận.
    # Vì vậy luật "nhận trước" (phần thứ 3 của khóa) đã được phần deadline
    # quyết định thay. Ticket nhận trước 1 phút có hạn sớm hơn 1 phút.
    q = TicketQueue()
    q.add(make_ticket("Z", at(9, 1), "high"))
    q.add(make_ticket("A", at(9, 0), "high"))
    assert ids(q.peek_top(2)) == ["A", "Z"]
    assert "hạn sớm hơn Z" in q.ranked_view(1)[0]["reason"]


def test_full_tie_falls_back_to_ticket_id():
    q = TicketQueue()
    for tid in ["T10", "T02", "T07"]:
        q.add(make_ticket(tid, at(9, 0), "medium"))
    assert ids(q.peek_top(3)) == ["T02", "T07", "T10"]
    assert "mã ticket đứng trước" in q.ranked_view(1)[0]["reason"]


def test_different_timezones_compare_by_real_instant():
    # 02:00 UTC chính là 09:00 +07:00 -> hai ticket cùng thời điểm thật.
    utc_ticket = make_ticket("B", datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc), "high")
    vn_ticket = make_ticket("A", at(9, 0), "high")
    q = TicketQueue()
    q.add(utc_ticket)
    q.add(vn_ticket)
    assert ids(q.peek_top(2)) == ["A", "B"]


def test_top_k_larger_than_queue_returns_all():
    assert ids(sample_queue().peek_top(10)) == ["T02", "T01", "T03"]


def test_many_tickets_stay_sorted():
    rng = random.Random(42)
    severities = ["low", "medium", "high", "critical"]
    q = TicketQueue()
    for i in range(10_000):
        q.add(make_ticket(f"T{i:05d}", at(0) + timedelta(minutes=rng.randint(0, 2000)), rng.choice(severities)))
    popped = [q.pop_next() for _ in range(len(q))]
    keys = [t.sort_key() for t in popped]
    assert keys == sorted(keys)


# --- Dữ liệu sai ------------------------------------------------------------

def test_duplicate_ticket_id_is_rejected():
    q = sample_queue()
    with pytest.raises(DuplicateTicketError):
        q.add(make_ticket("T01", at(10, 0), "low"))
    assert len(q) == 3  # ticket trùng không lọt vào hàng đợi


def test_duplicate_id_rejected_even_after_popped():
    q = sample_queue()
    q.pop_next()  # lấy T02 ra
    with pytest.raises(DuplicateTicketError):
        q.add(make_ticket("T02", at(10, 0), "low"))


def test_datetime_without_timezone_is_rejected():
    with pytest.raises(NaiveDatetimeError):
        make_ticket("T01", datetime(2026, 10, 21, 9, 0), "high")


@pytest.mark.parametrize("bad", ["urgent", "HIGH", "", "4"])
def test_unknown_severity_is_rejected(bad):
    with pytest.raises(InvalidSeverityError):
        make_ticket("T01", at(9, 0), bad)


def test_empty_ticket_id_is_rejected():
    with pytest.raises(TicketError):
        make_ticket("  ", at(9, 0), "high")


def test_top_k_must_be_positive():
    with pytest.raises(TicketError):
        sample_queue().peek_top(0)
