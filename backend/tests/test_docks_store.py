"""Test database Bài 02 trên SQLite tạm. Ràng buộc PostgreSQL kiểm tra bằng cách sinh DDL."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.core.database import as_utc, create_tables, make_session_factory
from app.core.entities import Run
from app.core.llm import LLMResult
from app.core.runs import create_run
from app.core.timeutil import get_tz
from app.modules.docks.algo import InvalidIntervalError, SlotRequest, suggest_slot
from app.modules.docks.entities import DockBooking, DockRequest
from app.modules.docks.store import (
    AlreadyBookedError,
    DockNotFoundError,
    SlotTakenError,
    create_booking,
    list_docks,
    load_bookings,
    new_request_id,
    seed_docks,
)

VN = get_tz("Asia/Ho_Chi_Minh")


def at(hour: int, minute: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=VN)


@pytest.fixture
def session_factory(tmp_path):
    engine, factory = make_session_factory(f"sqlite:///{tmp_path}/docks.db")
    create_tables(engine)
    with factory() as db:
        seed_docks(db, VN)
    yield factory
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


def day_bookings(db, day: int = 21):
    return load_bookings(db, at(0, 0, day), at(0, 0, day + 1), VN)


def suggest(db, earliest: datetime, minutes: int, dock_type: str = "standard"):
    return suggest_slot(SlotRequest(earliest, minutes, dock_type), list_docks(db), day_bookings(db), VN)


# --- Ca thường --------------------------------------------------------------

def test_seed_creates_docks_and_exercise_bookings(db):
    assert [(d.dock_id, d.dock_type) for d in list_docks(db)] == [("A", "standard"), ("B", "standard"), ("C", "refrigerated")]
    assert [(b.dock_id, b.start.strftime("%H:%M"), b.end.strftime("%H:%M")) for b in day_bookings(db)] == [
        ("A", "09:00", "10:00"), ("A", "10:00", "11:00"), ("B", "09:30", "10:15"), ("C", "10:00", "11:00"),
    ]


def test_seed_runs_only_once(db):
    assert seed_docks(db, VN) is False
    assert len(day_bookings(db)) == 4


def test_request_ids_are_sequential(db):
    assert [new_request_id(db) for _ in range(3)] == ["R001", "R002", "R003"]


def test_loaded_times_are_in_warehouse_timezone(db):
    first = day_bookings(db)[0]
    assert first.start == at(9, 0)
    assert first.start.utcoffset() == timedelta(hours=7)


def test_algorithm_on_seeded_data_gives_b_at_10_15(db):
    result = suggest(db, at(9, 0), 60)
    assert (result.dock_id, result.start) == ("B", at(10, 15))


def test_booking_is_saved_and_changes_next_suggestion(db):
    request_id = new_request_id(db)
    booking = create_booking(db, request_id, "B", "standard", at(10, 15), at(11, 15), "ACME Logistics")
    assert as_utc(booking.start_at) == datetime(2026, 10, 21, 3, 15, tzinfo=timezone.utc)  # lưu theo UTC
    assert db.scalar(select(DockRequest).where(DockRequest.request_id == request_id)).status == "booked"
    # Hỏi lại cùng giờ: B đã kín tới 11:15 -> chọn A 11:00
    result = suggest(db, at(9, 0), 60)
    assert (result.dock_id, result.start) == ("A", at(11, 0))


def test_booking_writes_result_to_run_history(db):
    request_id = new_request_id(db)
    run = create_run(db, "docks", {"email": "..."}, LLMResult({"duration_minutes": 60}, "mock", 3), issues=[])
    create_booking(db, request_id, "B", "standard", at(10, 15), at(11, 15), run=run, user_output={"duration_minutes": 60})
    saved = db.get(Run, run.id)
    assert saved.status == "confirmed"
    assert saved.result["dock_id"] == "B"
    assert saved.result["start"] == "2026-10-21T10:15:00+07:00"


def test_bookings_of_other_days_are_not_loaded(db):
    create_booking(db, new_request_id(db), "A", "standard", at(9, 0, day=22), at(10, 0, day=22))
    assert len(day_bookings(db, 21)) == 4
    assert len(day_bookings(db, 22)) == 1


# --- Ca sát giới hạn --------------------------------------------------------

def test_touching_booking_is_allowed(db):
    # B có lịch tới 10:15; đặt đúng 10:15 là hợp lệ vì khoảng là [start, end)
    booking = create_booking(db, new_request_id(db), "B", "standard", at(10, 15), at(11, 0))
    assert booking.id is not None


def test_overlap_by_one_minute_is_rejected(db):
    with pytest.raises(SlotTakenError) as exc:
        create_booking(db, new_request_id(db), "B", "standard", at(10, 14), at(11, 0))
    assert "09:30-10:15" in str(exc.value)


def test_cleanup_buffer_makes_touching_booking_conflict(db):
    with pytest.raises(SlotTakenError):
        create_booking(db, new_request_id(db), "B", "standard", at(10, 15), at(11, 0), cleanup=timedelta(minutes=15))
    assert create_booking(db, new_request_id(db), "B", "standard", at(10, 30), at(11, 0), cleanup=timedelta(minutes=15))


# --- Dữ liệu sai / hai người cùng đặt ---------------------------------------

def test_second_person_booking_same_slot_is_rejected(session_factory):
    # Hai người cùng được gợi ý B 10:15 rồi lần lượt bấm xác nhận
    with session_factory() as db1, session_factory() as db2:
        first, second = new_request_id(db1), new_request_id(db2)
        create_booking(db1, first, "B", "standard", at(10, 15), at(11, 15))
        with pytest.raises(SlotTakenError):
            create_booking(db2, second, "B", "standard", at(10, 15), at(11, 15))
    with session_factory() as db:
        assert len([b for b in day_bookings(db) if b.dock_id == "B"]) == 2  # lịch cũ + người đầu


def test_same_request_cannot_be_booked_twice(db):
    request_id = new_request_id(db)
    create_booking(db, request_id, "A", "standard", at(13, 0), at(14, 0))
    with pytest.raises(AlreadyBookedError):
        create_booking(db, request_id, "B", "standard", at(15, 0), at(16, 0))


def test_unknown_dock_is_rejected(db):
    with pytest.raises(DockNotFoundError):
        create_booking(db, new_request_id(db), "Z", "standard", at(13, 0), at(14, 0))


def test_wrong_dock_type_is_rejected(db):
    with pytest.raises(DockNotFoundError) as exc:
        create_booking(db, new_request_id(db), "C", "standard", at(13, 0), at(14, 0))
    assert "refrigerated" in str(exc.value)


def test_end_before_start_is_rejected(db):
    with pytest.raises(InvalidIntervalError):
        create_booking(db, new_request_id(db), "A", "standard", at(14, 0), at(13, 0))


# --- Ràng buộc trên PostgreSQL (Supabase) -----------------------------------

def test_postgres_ddl_has_exclusion_constraint():
    ddl = str(CreateTable(DockBooking.__table__).compile(dialect=postgresql.dialect()))
    assert "EXCLUDE USING gist (dock_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&)" in ddl
    assert "CHECK (end_at > start_at)" in ddl


def test_sqlite_ddl_skips_postgres_only_constraint():
    from sqlalchemy.dialects import sqlite
    ddl = str(CreateTable(DockBooking.__table__).compile(dialect=sqlite.dialect()))
    assert "EXCLUDE" not in ddl
