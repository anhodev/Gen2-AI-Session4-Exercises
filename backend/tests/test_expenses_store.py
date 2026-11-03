"""Test database Bài 03 trên SQLite tạm: cấp mã, lưu kết quả, kiểm tra lại, duyệt, dựng lại bảng tra cứu."""

from datetime import date

import pytest

from app.core.database import create_tables, make_session_factory
from app.core.entities import Run
from app.core.llm import LLMResult
from app.core.runs import create_run
from app.modules.expenses.algo import DUPLICATE, NEEDS_REVIEW, UNIQUE, Expense, ExpenseIndex, InvalidAmountError
from app.modules.expenses.entities import ExpenseReview, ExpenseSubmission
from app.modules.expenses.schemas import ExpenseExtraction
from app.modules.expenses.store import (
    AlreadyCheckedError,
    InvalidReviewError,
    NotCheckedError,
    SubmissionNotFoundError,
    attach_run,
    get_submission,
    list_reviews,
    load_index,
    new_submission_id,
    recheck,
    review,
    save_check,
)


@pytest.fixture
def session_factory(tmp_path):
    engine, factory = make_session_factory(f"sqlite:///{tmp_path}/expenses.db")
    create_tables(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


def extraction(submission_id: str, **overrides) -> ExpenseExtraction:
    data = {
        "submission_id": submission_id, "vendor": "ABC Travel", "invoice_number": "INV-0042",
        "transaction_date": "2026-10-19", "amount_minor": 1250000, "currency": "VND",
        "receipt_id": "R-42", "missing_fields": [],
    }
    data.update(overrides)
    return ExpenseExtraction.model_validate(data)


def submit(db, index, **overrides):
    """Cấp mã rồi kiểm tra trùng, giống luồng extract -> check."""
    sid = new_submission_id(db)
    return sid, save_check(db, index, extraction(sid, **overrides))


# --- Cấp mã -----------------------------------------------------------------

def test_submission_ids_are_sequential(db):
    assert [new_submission_id(db) for _ in range(3)] == ["E01", "E02", "E03"]
    assert get_submission(db, "E02").status == "new"


def test_attach_run(db):
    sid = new_submission_id(db)
    run = create_run(db, "expenses", {"text": "x"}, LLMResult({}, "mock", 0), [])
    attach_run(db, sid, run)
    assert get_submission(db, sid).run_id == run.id


# --- Kiểm tra trùng và lưu --------------------------------------------------

def test_exercise_flow_is_saved(db):
    index = ExpenseIndex()
    _, r1 = submit(db, index)
    _, r2 = submit(db, index, vendor="abc travel", invoice_number="inv-0042")
    _, r3 = submit(db, index, invoice_number=None, receipt_id=None, missing_fields=["invoice_number", "receipt_id"])
    assert [r1.status, r2.status, r3.status] == [UNIQUE, DUPLICATE, NEEDS_REVIEW]

    rows = {s: get_submission(db, s) for s in ("E01", "E02", "E03")}
    assert (rows["E01"].check_status, rows["E01"].review_status) == (UNIQUE, None)
    assert (rows["E02"].matched_ids, rows["E02"].review_status) == (["E01"], "pending")
    assert rows["E03"].review_status == "pending"
    assert rows["E02"].vendor == "abc travel"  # lưu bản người dùng xác nhận, không lưu bản chuẩn hóa
    assert rows["E01"].transaction_date == date(2026, 10, 19)
    assert len(index) == 3


def test_run_keeps_before_and_after_edit(db):
    index = ExpenseIndex()
    sid = new_submission_id(db)
    llm = extraction(sid, receipt_id="R-4Z").model_dump(mode="json")  # GenAI đọc nhầm
    run = create_run(db, "expenses", {"text": "..."}, LLMResult(llm, "mock", 5), [])
    save_check(db, index, extraction(sid), run)  # người dùng sửa thành R-42
    db.refresh(run)
    assert run.status == "confirmed"
    assert run.llm_output["receipt_id"] == "R-4Z"
    assert run.user_output["receipt_id"] == "R-42"
    assert run.result["status"] == UNIQUE
    assert run.result["keys"][0] == ["receipt", "VND", "r-42"]
    assert run.result["edited_fields"] == ["receipt_id"]


def test_check_twice_is_rejected(db):
    index = ExpenseIndex()
    sid, _ = submit(db, index)
    with pytest.raises(AlreadyCheckedError):
        save_check(db, index, extraction(sid))


def test_check_unknown_submission_is_rejected(db):
    with pytest.raises(SubmissionNotFoundError):
        save_check(db, ExpenseIndex(), extraction("E99"))


def test_bad_data_changes_nothing(db):
    index = ExpenseIndex()
    sid = new_submission_id(db)
    data = extraction(sid).model_copy(update={"amount_minor": -1})  # lách qua schema
    with pytest.raises(InvalidAmountError):
        save_check(db, index, data)
    assert get_submission(db, sid).status == "new"
    assert len(index) == 0


def test_commit_failure_does_not_touch_index(db, monkeypatch):
    index = ExpenseIndex()
    sid = new_submission_id(db)

    def boom():
        raise RuntimeError("mất kết nối database")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        save_check(db, index, extraction(sid))
    assert len(index) == 0


# --- Kiểm tra lại sau khi sửa -----------------------------------------------

def test_recheck_after_editing_receipt_logs_before_and_after(db):
    index = ExpenseIndex()
    submit(db, index, invoice_number="INV-1")
    sid, first = submit(db, index, invoice_number="INV-2")  # trùng R-42 với E01
    assert first.status == DUPLICATE

    result = recheck(db, index, extraction(sid, invoice_number="INV-2", receipt_id="R-43"), note="mã đúng là R-43")
    assert result.status == UNIQUE
    row = get_submission(db, sid)
    assert (row.receipt_id, row.check_status, row.review_status, row.matched_ids) == ("R-43", UNIQUE, None, [])

    log = list_reviews(db, sid)[-1]
    assert (log.action, log.before["receipt_id"], log.after["receipt_id"]) == ("recheck", "R-42", "R-43")
    assert log.result["status"] == UNIQUE
    assert log.note == "mã đúng là R-43"
    # Bảng tra cứu đã cập nhật: R-42 chỉ còn E01
    assert index.check(index.get("E01")).status == UNIQUE


def test_recheck_can_turn_unique_into_duplicate(db):
    index = ExpenseIndex()
    submit(db, index)
    sid, _ = submit(db, index, invoice_number="INV-9", receipt_id="R-99")
    result = recheck(db, index, extraction(sid, invoice_number="INV-9", receipt_id="R-42"))
    assert (result.status, [m.submission_id for m in result.matches]) == (DUPLICATE, ["E01"])
    assert get_submission(db, sid).review_status == "pending"


def test_recheck_before_first_check_is_rejected(db):
    sid = new_submission_id(db)
    with pytest.raises(NotCheckedError):
        recheck(db, ExpenseIndex(), extraction(sid))


def test_recheck_with_bad_data_keeps_old_entry(db):
    index = ExpenseIndex()
    sid, _ = submit(db, index)
    with pytest.raises(InvalidAmountError):
        recheck(db, index, extraction(sid).model_copy(update={"amount_minor": -5}))
    assert index.get(sid).amount_minor == 1250000
    assert get_submission(db, sid).amount_minor == 1250000


def test_recheck_commit_failure_restores_index(db, monkeypatch):
    index = ExpenseIndex()
    sid, _ = submit(db, index)
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("mất kết nối")))
    with pytest.raises(RuntimeError):
        recheck(db, index, extraction(sid, receipt_id="R-43"))
    assert index.get(sid).receipt_id == "R-42"  # bảng tra cứu trả về như cũ


# --- Người duyệt ------------------------------------------------------------

def test_confirm_duplicate(db):
    index = ExpenseIndex()
    submit(db, index)
    sid, _ = submit(db, index)
    row = review(db, sid, "confirm_duplicate", note="cùng vé máy bay")
    assert (row.review_status, row.reviewed_at is not None) == ("confirmed_duplicate", True)
    log = list_reviews(db, sid)[-1]
    assert (log.action, log.note, log.before == log.after) == ("confirm_duplicate", "cùng vé máy bay", True)


def test_dismiss_needs_review(db):
    index = ExpenseIndex()
    sid, _ = submit(db, index, invoice_number=None, receipt_id=None, missing_fields=["invoice_number", "receipt_id"])
    assert review(db, sid, "dismiss").review_status == "dismissed"


def test_cannot_confirm_duplicate_without_match(db):
    index = ExpenseIndex()
    sid, _ = submit(db, index, invoice_number=None, receipt_id=None, missing_fields=["invoice_number", "receipt_id"])
    with pytest.raises(InvalidReviewError):
        review(db, sid, "confirm_duplicate")


def test_nothing_to_review_for_unique(db):
    sid, _ = submit(db, ExpenseIndex())
    with pytest.raises(InvalidReviewError):
        review(db, sid, "dismiss")


def test_unknown_review_action_is_rejected(db):
    with pytest.raises(InvalidReviewError):
        review(db, "E01", "reject_refund")  # hệ thống không có thao tác từ chối hoàn tiền


def test_review_before_check_is_rejected(db):
    sid = new_submission_id(db)
    with pytest.raises(NotCheckedError):
        review(db, sid, "dismiss")


def test_review_does_not_change_index(db):
    index = ExpenseIndex()
    submit(db, index)
    sid, _ = submit(db, index)
    review(db, sid, "dismiss")
    # Bỏ nghi vấn không xóa nhãn: bản gửi lần 3 vẫn thấy cả E01 và E02
    _, third = submit(db, index)
    assert [m.submission_id for m in third.matches] == ["E01", "E02"]


# --- Dựng lại bảng tra cứu sau khi khởi động lại ----------------------------

def test_index_survives_restart(session_factory):
    with session_factory() as db:
        index = ExpenseIndex()
        submit(db, index)
        submit(db, index, vendor="abc travel")
        new_submission_id(db)  # cấp mã nhưng chưa kiểm tra: không vào bảng tra cứu
    with session_factory() as db:  # khởi động lại
        index = load_index(db)
        assert len(index) == 2
        sid = new_submission_id(db)
        result = save_check(db, index, extraction(sid))
        assert (sid, [m.submission_id for m in result.matches]) == ("E04", ["E01", "E02"])


def test_index_order_follows_check_order_after_recheck(session_factory):
    with session_factory() as db:
        index = ExpenseIndex()
        submit(db, index)                                   # E01: R-42
        sid, _ = submit(db, index, receipt_id="R-1", invoice_number="INV-1")
        recheck(db, index, extraction(sid, receipt_id="R-42", invoice_number="INV-1"))  # E02 thành R-42 sau
    with session_factory() as db:
        index = load_index(db)
        result = index.check(Expense("E09", None, None, None, None, "VND", "R-42"))
        assert [m.submission_id for m in result.matches] == ["E01", "E02"]


# --- Bảng trong database ----------------------------------------------------

def test_tables_are_created(db):
    assert db.query(ExpenseSubmission).count() == 0
    assert db.query(ExpenseReview).count() == 0
    assert db.query(Run).count() == 0
