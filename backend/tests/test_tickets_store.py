"""Test lưu lịch sử và dựng lại hàng đợi từ database. Mỗi test dùng một file SQLite tạm."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import as_utc, create_tables, make_session_factory, to_utc
from app.core.entities import Run
from app.core.llm import LLMResult
from app.core.runs import create_run
from app.modules.tickets.algo import DuplicateTicketError
from app.modules.tickets.entities import Ticket
from app.modules.tickets.schemas import TicketExtraction
from app.modules.tickets.store import load_queue, mark_done, new_ticket_id, save_ticket

VN = timezone(timedelta(hours=7))


@pytest.fixture
def session_factory(tmp_path):
    engine, factory = make_session_factory(f"sqlite:///{tmp_path}/data/test.db")
    create_tables(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


def extraction(ticket_id: str, severity: str, **overrides) -> TicketExtraction:
    data = {
        "ticket_id": ticket_id, "intent": "billing", "severity": severity,
        "product": None, "summary": f"Ticket {ticket_id}", "needs_human_review": False,
    }
    data.update(overrides)
    return TicketExtraction(**data)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 21, hour, minute, tzinfo=VN)


def new_run(db, llm_output: dict) -> Run:
    return create_run(db, "tickets", {"message": "..."}, LLMResult(llm_output, "mock", 12), issues=[])


# --- Ca thường --------------------------------------------------------------

def test_data_folder_is_created(tmp_path):
    make_session_factory(f"sqlite:///{tmp_path}/new_folder/app.db")
    assert (tmp_path / "new_folder").is_dir()


def test_run_keeps_input_llm_output_model_and_latency(db):
    run = new_run(db, {"severity": "high"})
    saved = db.get(Run, run.id)
    assert saved.status == "extracted"
    assert saved.raw_input == {"message": "..."}
    assert saved.llm_output == {"severity": "high"}
    assert (saved.model, saved.latency_ms) == ("mock", 12)


def test_queue_survives_restart(session_factory):
    # Phiên 1: xác nhận 3 ticket trong đề
    with session_factory() as db:
        save_ticket(db, extraction("T01", "high"), at(9, 0))
        save_ticket(db, extraction("T02", "critical"), at(9, 20))
        save_ticket(db, extraction("T03", "medium"), at(8, 30))

    # Phiên 2: giả lập app khởi động lại, đọc từ database
    with session_factory() as db:
        queue = load_queue(db)
    assert [t.ticket_id for t in queue.peek_top(3)] == ["T02", "T01", "T03"]


def test_confirm_writes_user_edit_and_result_to_run(db):
    run = new_run(db, extraction("T01", "medium").model_dump())
    save_ticket(db, extraction("T01", "high"), at(9, 0), run)  # người dùng sửa medium -> high

    saved = db.get(Run, run.id)
    assert saved.status == "confirmed"
    assert saved.user_output["severity"] == "high"
    assert saved.result["deadline_at"] == "2026-10-21T11:00:00+07:00"
    assert saved.result["edited_fields"] == ["severity"]
    assert saved.confirmed_at is not None
    assert db.get(Ticket, "T01").run_id == run.id


def test_done_ticket_is_not_loaded_back(db):
    save_ticket(db, extraction("T01", "high"), at(9, 0))
    save_ticket(db, extraction("T02", "critical"), at(9, 20))
    mark_done(db, "T02")
    assert [t.ticket_id for t in load_queue(db).peek_top(5)] == ["T01"]
    assert db.get(Ticket, "T02").popped_at is not None


# --- Ca sát giới hạn --------------------------------------------------------

def test_time_is_stored_as_utc_and_keeps_the_same_instant(db):
    save_ticket(db, extraction("T01", "high"), at(9, 0))  # 09:00 +07:00 = 02:00 UTC
    db.expire_all()  # bắt buộc đọc lại từ database, không dùng object trong bộ nhớ
    row = db.get(Ticket, "T01")
    assert as_utc(row.received_at) == datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)
    assert as_utc(row.deadline_at) == at(11, 0)  # so sánh theo thời điểm thật, khác múi giờ vẫn bằng


def test_mixed_timezones_still_sorted_by_real_instant(session_factory):
    with session_factory() as db:
        save_ticket(db, extraction("VN", "high"), at(9, 0))  # 02:00 UTC
        save_ticket(db, extraction("UTC", "high"), datetime(2026, 10, 21, 1, 30, tzinfo=timezone.utc))
    with session_factory() as db:
        assert [t.ticket_id for t in load_queue(db).peek_top(2)] == ["UTC", "VN"]


def test_to_utc_rejects_naive_datetime():
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 10, 21, 9, 0))


def test_mark_done_unknown_id_returns_none(db):
    assert mark_done(db, "T99") is None


# --- Dữ liệu sai ------------------------------------------------------------

def test_duplicate_id_is_rejected_and_logged(db):
    save_ticket(db, extraction("T01", "high"), at(9, 0))
    run = new_run(db, extraction("T01", "low").model_dump())

    with pytest.raises(DuplicateTicketError):
        save_ticket(db, extraction("T01", "low"), at(10, 0), run)

    saved = db.get(Run, run.id)
    assert saved.status == "rejected"
    assert "đã tồn tại" in saved.error
    assert db.get(Ticket, "T01").severity == "high"  # bản cũ không bị ghi đè


def test_duplicate_of_done_ticket_is_still_rejected(db):
    save_ticket(db, extraction("T01", "high"), at(9, 0))
    mark_done(db, "T01")
    with pytest.raises(DuplicateTicketError):
        save_ticket(db, extraction("T01", "high"), at(10, 0))


def test_new_ticket_ids_are_sequential(db):
    assert [new_ticket_id(db) for _ in range(3)] == ["T01", "T02", "T03"]


def test_new_ticket_id_skips_existing_ticket(db):
    # T02 đã có sẵn (vd nhập tay trước đây) -> bộ sinh mã bỏ qua, không cấp trùng
    save_ticket(db, extraction("T02", "high"), at(9))
    assert [new_ticket_id(db) for _ in range(2)] == ["T01", "T03"]
