"""Test thuật toán Bài 01. Không gọi GenAI, không cần API key.

Chia 3 nhóm theo yêu cầu nộp bài: ca thường, ca sát giới hạn, dữ liệu sai.
"""

import itertools
import random
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.tickets.algo import (
    INTENTS,
    DuplicateTicketError,
    InvalidSeverityError,
    NaiveDatetimeError,
    Staff,
    StaffError,
    TicketError,
    TicketQueue,
    assign_tickets,
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


# --- Làm thêm: giao ticket cho nhân viên theo kỹ năng và số việc tối đa ----

def person(staff_id, name, skills, max_tickets):
    return Staff(staff_id, name, frozenset(skills), max_tickets)


TEAM = [
    person("NV01", "An", {"billing", "account"}, 2),
    person("NV02", "Bình", {"technical"}, 2),
    person("NV03", "Chi", {"technical", "account"}, 1),
]


def owners(result) -> dict:
    return {a.ticket_id: a.staff_id for a in result.assignments}


def is_valid(result, tickets, staff) -> bool:
    """Ai nhận ticket nào cũng đúng kỹ năng, không ai quá số việc tối đa, mỗi ticket tối đa 1 người."""
    by_id = {p.staff_id: p for p in staff}
    intent = dict(tickets)
    counted = [t for ids in result.loads.values() for t in ids]
    return (
        len(counted) == len(set(counted))
        and all(len(ids) <= by_id[s].max_tickets for s, ids in result.loads.items())
        and all(a.staff_id is None or intent[a.ticket_id] in by_id[a.staff_id].skills for a in result.assignments)
        and all(a.ticket_id in result.loads[a.staff_id] for a in result.assignments if a.staff_id)
    )


# --- Ca thường --------------------------------------------------------------

def test_each_ticket_goes_to_someone_with_the_skill():
    result = assign_tickets([("T01", "billing"), ("T02", "technical")], TEAM)
    assert owners(result) == {"T01": "NV01", "T02": "NV02"}
    assert result.unassigned == []


def test_least_busy_person_gets_the_ticket():
    # Bình và Chi đều làm technical; Bình đã có T01 nên T02 sang Chi
    result = assign_tickets([("T01", "technical"), ("T02", "technical")], TEAM)
    assert owners(result) == {"T01": "NV02", "T02": "NV03"}


def test_tie_goes_to_smaller_staff_id():
    team = [person("NV02", "Bình", {"billing"}, 1), person("NV01", "An", {"billing"}, 1)]
    assert owners(assign_tickets([("T01", "billing")], team)) == {"T01": "NV01"}


def test_no_one_with_skill_is_reported():
    result = assign_tickets([("T01", "other")], TEAM)
    assert result.unassigned[0].reason == "Chưa có nhân viên nào có kỹ năng other"


def test_reshuffle_frees_a_slot_instead_of_leaving_ticket_unassigned():
    # Giao kiểu tham lam: T03 (account) vào Chi vì Chi đang rảnh hơn An -> T05 (technical) hết người.
    # Đổi T03 sang An thì Chi nhận được T05: T05 không được báo "chưa có người nhận".
    tickets = [("T02", "technical"), ("T01", "billing"), ("T03", "account"), ("T04", "technical"), ("T05", "technical")]
    result = assign_tickets(tickets, TEAM)
    assert owners(result) == {"T02": "NV02", "T01": "NV01", "T03": "NV01", "T04": "NV02", "T05": "NV03"}
    assert result.unassigned == []


def test_less_urgent_ticket_is_the_one_left_out():
    # 5 chỗ, 7 ticket: T06 (other) không ai làm được; T07 (account) ít gấp nhất trong số còn lại nên hết chỗ
    tickets = [("T02", "technical"), ("T01", "billing"), ("T03", "account"), ("T04", "technical"),
               ("T05", "technical"), ("T06", "other"), ("T07", "account")]
    result = assign_tickets(tickets, TEAM)
    assert [a.ticket_id for a in result.unassigned] == ["T06", "T07"]
    assert result.unassigned[1].reason.startswith("Những người có kỹ năng account đều đã nhận đủ ticket gấp hơn (An 2/2, Chi 1/1)")
    assert result.loads == {"NV01": ["T01", "T03"], "NV02": ["T02", "T04"], "NV03": ["T05"]}


def test_assignments_follow_queue_order_and_reason_names_person():
    result = assign_tickets([("T02", "billing"), ("T01", "billing")], TEAM)
    assert [a.ticket_id for a in result.assignments] == ["T02", "T01"]
    assert result.assignments[0].reason == "An có kỹ năng billing, nhận 2/2 việc"


# --- Ca sát giới hạn --------------------------------------------------------

def test_empty_queue_and_empty_team():
    assert assign_tickets([], TEAM).assignments == []
    result = assign_tickets([("T01", "billing")], [])
    assert result.unassigned[0].reason == "Chưa có nhân viên nào có kỹ năng billing"


def test_max_zero_means_not_taking_tickets():
    team = [person("NV01", "An", {"billing"}, 0)]
    result = assign_tickets([("T01", "billing")], team)
    assert result.unassigned[0].reason.startswith("Những người có kỹ năng billing đều đã nhận đủ ticket gấp hơn (An 0/0)")


def test_exactly_full_capacity():
    team = [person("NV01", "An", {"billing"}, 2)]
    result = assign_tickets([("T01", "billing"), ("T02", "billing"), ("T03", "billing")], team)
    assert owners(result) == {"T01": "NV01", "T02": "NV01", "T03": None}


def test_long_reshuffle_chain():
    # T04 (a) chỉ An làm được; An giữ T01 (b) -> chuyển sang Bình; Bình giữ T02 (c) -> chuyển sang Chi
    team = [
        person("NV01", "An", {"billing", "technical"}, 1),
        person("NV02", "Bình", {"technical", "account"}, 1),
        person("NV03", "Chi", {"account"}, 1),
    ]
    tickets = [("T01", "technical"), ("T02", "account"), ("T03", "billing")]
    result = assign_tickets(tickets, team)
    assert owners(result) == {"T01": "NV02", "T02": "NV03", "T03": "NV01"}


def test_staff_ids_compare_by_number():
    team = [person("NV10", "Mười", {"billing"}, 1), person("NV2", "Hai", {"billing"}, 1)]
    assert owners(assign_tickets([("T01", "billing")], team)) == {"T01": "NV2"}


def test_same_result_when_staff_list_is_shuffled():
    tickets = [(f"T{i:02d}", INTENTS[i % 3]) for i in range(8)]
    expected = owners(assign_tickets(tickets, TEAM))
    rng = random.Random(0)
    for _ in range(20):
        team = TEAM[:]
        rng.shuffle(team)
        assert owners(assign_tickets(tickets, team)) == expected


def test_many_tickets_run_fast():
    rng = random.Random(1)
    team = [person(f"NV{i:02d}", f"NV {i}", set(rng.sample(INTENTS, rng.randint(1, 3))), rng.randint(0, 40)) for i in range(30)]
    tickets = [(f"T{i:05d}", rng.choice(INTENTS)) for i in range(5000)]
    started = time.perf_counter()
    result = assign_tickets(tickets, team)
    assert time.perf_counter() - started < 2.0
    assert is_valid(result, tickets, team)


# --- Dữ liệu sai ------------------------------------------------------------

def test_duplicate_staff_id_is_rejected():
    with pytest.raises(StaffError, match="bị trùng"):
        assign_tickets([], [TEAM[0], TEAM[0]])


def test_unknown_skill_is_rejected():
    with pytest.raises(StaffError, match="refund"):
        assign_tickets([], [person("NV01", "An", {"refund"}, 1)])


def test_negative_max_is_rejected():
    with pytest.raises(StaffError, match="không được âm"):
        assign_tickets([], [person("NV01", "An", {"billing"}, -1)])


def test_blank_staff_id_is_rejected():
    with pytest.raises(StaffError, match="rỗng"):
        assign_tickets([], [person(" ", "An", {"billing"}, 1)])


# --- Đối chiếu brute force --------------------------------------------------

def can_all_be_assigned(tickets, staff) -> bool:
    """Thử mọi cách giao: mỗi ticket chọn một người đúng kỹ năng, không ai quá số việc tối đa."""
    choices = [[p.staff_id for p in staff if intent in p.skills] for _, intent in tickets]
    limit = {p.staff_id: p.max_tickets for p in staff}
    for combo in itertools.product(*choices):
        if all(combo.count(s) <= limit[s] for s in set(combo)):
            return True
    return False


@pytest.mark.parametrize("seed", range(150))
def test_matches_brute_force_on_random_data(seed):
    # Ticket i có người nhận <=> giao được cùng lúc ticket i và mọi ticket gấp hơn đã có người nhận.
    # Tức là không ticket nào bị bỏ oan, và ticket gấp luôn được ưu tiên.
    rng = random.Random(seed)
    team = [person(f"NV{i}", f"NV {i}", set(rng.sample(INTENTS, rng.randint(1, 2))), rng.randint(0, 2))
            for i in range(rng.randint(1, 4))]
    tickets = [(f"T{i:02d}", rng.choice(INTENTS)) for i in range(rng.randint(1, 7))]
    result = assign_tickets(tickets, team)
    assert is_valid(result, tickets, team)

    kept = []
    for ticket, a in zip(tickets, result.assignments):
        possible = can_all_be_assigned(kept + [ticket], team)
        assert (a.staff_id is not None) == possible, (tickets, team)
        if possible:
            kept.append(ticket)
