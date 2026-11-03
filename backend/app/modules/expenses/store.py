"""Bài 03 - Đọc/ghi chứng từ trong database.

Bảng tra cứu (hash map) nằm trong bộ nhớ để tìm nhanh. Database giữ bản lâu dài:
khi app khởi động lại, load_index() đọc các chứng từ đã kiểm tra và dựng lại bảng.

Thứ tự khi ghi: commit database thành công rồi mới sửa bảng tra cứu,
để commit lỗi thì bảng tra cứu vẫn khớp với database.
Người gọi giữ khóa (app.state.expense_lock) quanh các hàm có sửa bảng tra cứu.
"""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entities import Run, now_utc
from app.core.runs import mark_confirmed

from .algo import DUPLICATE, NEEDS_REVIEW, CheckResult, Expense, ExpenseError, ExpenseIndex, from_extraction
from .entities import ExpenseReview, ExpenseSubmission
from .schemas import ExpenseExtraction

DATA_FIELDS = ("vendor", "invoice_number", "transaction_date", "amount_minor", "currency", "receipt_id")


class SubmissionNotFoundError(ExpenseError):
    """Không có chứng từ với mã này."""


class AlreadyCheckedError(ExpenseError):
    """Chứng từ đã được kiểm tra trùng (muốn sửa thì dùng kiểm tra lại)."""


class NotCheckedError(ExpenseError):
    """Chứng từ chưa được kiểm tra trùng."""


class InvalidReviewError(ExpenseError):
    """Thao tác duyệt không hợp lệ với trạng thái hiện tại."""


# --- Cấp mã -----------------------------------------------------------------

def new_submission_id(db: Session) -> str:
    """
    Sinh mã chứng từ mới: E01, E02... theo số tự tăng của database (quá 99 thì thành E100).

    Returns:
        str: Mã chứng từ.
    """
    row = ExpenseSubmission(created_at=now_utc())
    db.add(row)
    db.flush()  # lấy id tự tăng trước khi commit
    row.submission_id = f"E{row.id:02d}"
    db.commit()
    return row.submission_id


def attach_run(db: Session, submission_id: str, run: Run) -> None:
    """Ghi lại lần extract tương ứng với mã chứng từ."""
    row = get_submission(db, submission_id)
    if row is not None:
        row.run_id = run.id
        db.commit()


def get_submission(db: Session, submission_id: str) -> ExpenseSubmission | None:
    return db.scalar(select(ExpenseSubmission).where(ExpenseSubmission.submission_id == submission_id))


# --- Kiểm tra trùng ---------------------------------------------------------

def save_check(db: Session, index: ExpenseIndex, data: ExpenseExtraction, run: Run | None = None) -> CheckResult:
    """
    Kiểm tra trùng lần đầu cho bản người dùng đã xem/sửa, lưu kết quả, rồi thêm vào bảng tra cứu.

    Args:
        db (Session): Database session.
        index (ExpenseIndex): Bảng tra cứu đang dùng.
        data (ExpenseExtraction): Bản đã kiểm tra 2 lớp, không còn lỗi.
        run (Run | None, optional): Lần extract tương ứng, để ghi kết quả vào lịch sử.

    Raises:
        SubmissionNotFoundError: Mã chứng từ chưa được cấp.
        AlreadyCheckedError: Chứng từ đã được kiểm tra rồi.
        ExpenseError: Dữ liệu sai (tiền âm, loại tiền chưa hỗ trợ...).

    Returns:
        CheckResult: Trạng thái, các chứng từ cũ trùng và lý do.
    """
    row = get_submission(db, data.submission_id)
    if row is None:
        raise SubmissionNotFoundError(f"Không có chứng từ {data.submission_id}")
    if row.status == "checked":
        raise AlreadyCheckedError(f"Chứng từ {data.submission_id} đã được kiểm tra trùng")

    expense = from_extraction(data)  # kiểm tra lại tiền, loại tiền
    result = index.check(expense)

    write_result(row, expense, result)
    if run is not None:
        mark_confirmed(run, data.model_dump(mode="json"), result=result_to_dict(result))
    try:
        db.commit()
    except Exception:
        db.rollback()  # chưa thêm vào bảng tra cứu nên không cần trả lại
        raise

    index.add(expense)  # chỉ thêm khi đã lưu xong
    return result


def recheck(
    db: Session,
    index: ExpenseIndex,
    data: ExpenseExtraction,
    note: str | None = None,
) -> CheckResult:
    """
    Người duyệt sửa thông tin (vd mã biên nhận) rồi kiểm tra trùng lại.

    Bỏ bản cũ khỏi bảng tra cứu, kiểm tra bản mới, lưu kết quả và nhật ký trước/sau khi sửa.

    Raises:
        SubmissionNotFoundError: Không có chứng từ này.
        NotCheckedError: Chứng từ chưa được kiểm tra lần đầu.
        ExpenseError: Dữ liệu mới sai.

    Returns:
        CheckResult: Kết quả kiểm tra lại.
    """
    row = get_submission(db, data.submission_id)
    if row is None:
        raise SubmissionNotFoundError(f"Không có chứng từ {data.submission_id}")
    if row.status != "checked":
        raise NotCheckedError(f"Chứng từ {data.submission_id} chưa được kiểm tra trùng lần nào")

    expense = from_extraction(data)  # kiểm tra trước khi động vào bảng tra cứu
    before = submission_to_dict(row)
    old = index.remove(data.submission_id)
    try:
        result = index.check(expense)
        write_result(row, expense, result)
        db.add(ExpenseReview(
            submission_id=data.submission_id, action="recheck",
            before=before, after=submission_to_dict(row), result=result_to_dict(result),
            note=note, created_at=now_utc(),
        ))
        db.commit()
    except Exception:
        # Lưu lỗi: trả bảng tra cứu về như cũ
        db.rollback()
        if old is not None:
            index.add(old)
        raise

    index.add(expense)
    return result


def write_result(row: ExpenseSubmission, expense: Expense, result: CheckResult) -> None:
    """Ghi dữ liệu chứng từ và kết quả kiểm tra vào dòng trong database. Người gọi tự commit."""
    for name in DATA_FIELDS:
        setattr(row, name, getattr(expense, name))
    row.status = "checked"
    row.check_status = result.status
    row.matched_ids = [m.submission_id for m in result.matches]
    # Trùng hoặc thiếu dữ liệu thì chờ người duyệt; chưa trùng thì không cần
    row.review_status = "pending" if result.status in (DUPLICATE, NEEDS_REVIEW) else None
    row.checked_at = now_utc()
    row.reviewed_at = None


# --- Người duyệt ------------------------------------------------------------

def review(db: Session, submission_id: str, action: str, note: str | None = None) -> ExpenseSubmission:
    """
    Người duyệt xác nhận trùng hoặc bỏ nghi vấn. Không sửa dữ liệu, không đổi bảng tra cứu.

    Hệ thống không tự kết luận: chỉ người duyệt mới đổi được trạng thái này.

    Args:
        action (str): "confirm_duplicate" hoặc "dismiss".

    Raises:
        SubmissionNotFoundError: Không có chứng từ này.
        NotCheckedError: Chưa kiểm tra trùng.
        InvalidReviewError: Không có gì để duyệt, hoặc xác nhận trùng khi không có bản trùng.

    Returns:
        ExpenseSubmission: Dòng đã cập nhật.
    """
    if action not in ("confirm_duplicate", "dismiss"):
        raise InvalidReviewError(f"Thao tác '{action}' không hợp lệ")
    row = get_submission(db, submission_id)
    if row is None:
        raise SubmissionNotFoundError(f"Không có chứng từ {submission_id}")
    if row.status != "checked":
        raise NotCheckedError(f"Chứng từ {submission_id} chưa được kiểm tra trùng")
    if row.review_status is None:
        raise InvalidReviewError(f"Chứng từ {submission_id} chưa trùng theo quy tắc hiện tại, không có gì để duyệt")
    # Có chứng từ cũ (trùng hoặc gần giống) thì người duyệt mới xác nhận trùng được
    if action == "confirm_duplicate" and not row.matched_ids:
        raise InvalidReviewError(f"Chứng từ {submission_id} không có bản trùng để xác nhận")

    snapshot = submission_to_dict(row)
    row.review_status = "confirmed_duplicate" if action == "confirm_duplicate" else "dismissed"
    row.reviewed_at = now_utc()
    db.add(ExpenseReview(
        submission_id=submission_id, action=action, before=snapshot, after=snapshot,
        note=note, created_at=now_utc(),
    ))
    db.commit()
    return row


def list_reviews(db: Session, submission_id: str) -> list[ExpenseReview]:
    """Nhật ký duyệt của một chứng từ, cũ trước."""
    return list(db.scalars(
        select(ExpenseReview).where(ExpenseReview.submission_id == submission_id).order_by(ExpenseReview.id)
    ))


# --- Dựng lại bảng tra cứu --------------------------------------------------

def load_index(db: Session) -> ExpenseIndex:
    """
    Dựng lại bảng tra cứu từ các chứng từ đã kiểm tra, theo thứ tự kiểm tra
    (để "bản gửi sớm nhất đứng đầu" vẫn đúng sau khi khởi động lại).

    Returns:
        ExpenseIndex: Bảng tra cứu chứa mọi chứng từ có status="checked".
    """
    index = ExpenseIndex()
    rows = db.scalars(
        select(ExpenseSubmission)
        .where(ExpenseSubmission.status == "checked")
        .order_by(ExpenseSubmission.checked_at, ExpenseSubmission.id)
    )
    for row in rows:
        index.add(Expense(row.submission_id, *(getattr(row, name) for name in DATA_FIELDS)))
    return index


# --- Đổi sang JSON để lưu ---------------------------------------------------

def submission_to_dict(row: ExpenseSubmission) -> dict:
    """Thông tin chứng từ dạng JSON (ngày thành chuỗi YYYY-MM-DD)."""
    data = {"submission_id": row.submission_id}
    for name in DATA_FIELDS:
        value = getattr(row, name)
        data[name] = value.isoformat() if name == "transaction_date" and value is not None else value
    return data


def result_to_dict(result: CheckResult) -> dict:
    """Kết quả thuật toán dạng JSON. Nhãn tra cứu (tuple) thành list."""
    data = asdict(result)
    data["keys"] = [list(key) for key in result.keys]
    return data
