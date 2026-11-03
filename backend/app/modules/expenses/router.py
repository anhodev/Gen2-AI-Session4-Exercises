"""Bài 03 - API kiểm tra chứng từ có thể đã được gửi trước đó.

  GET  /expenses/samples              Chứng từ mẫu (văn bản đã OCR) để chạy thử ở chế độ mock.
  GET  /expenses/sample-pdf           Một PDF mẫu có lớp chữ, để thử chức năng tải PDF.
  POST /expenses/pdf-text             Tải PDF lên -> lấy chữ, để người dùng xem lại trước khi cho GenAI đọc.
  POST /expenses/extract              Backend cấp mã chứng từ, GenAI đọc văn bản, code kiểm tra. CHƯA tìm trùng.
  POST /expenses/check                Người dùng đã xem/sửa -> lưu và tìm bản có thể trùng.
  POST /expenses/review               Người duyệt xác nhận trùng, bỏ nghi vấn, hoặc sửa rồi kiểm tra lại.
  GET  /expenses/submissions          Các chứng từ đã kiểm tra, mới nhất trước.
  GET  /expenses/submissions/{id}     Một chứng từ kèm nhật ký duyệt.

Hệ thống chỉ giúp người duyệt kiểm tra: không tự kết luận gian lận, không tự từ chối hoàn tiền.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import as_utc
from app.core.dependencies import get_app_settings, get_db, get_display_tz
from app.core.entities import Run
from app.core.llm import SAMPLES_DIR, load_samples
from app.core.runs import create_run

from .algo import CheckResult, ExpenseError, format_amount
from .entities import ExpenseSubmission
from .extractor import TASK, extract_expense
from .pdf import MAX_PDF_BYTES, PdfTextError, pdf_to_text
from .schemas import (
    CheckOut,
    CheckRequest,
    ExpenseExtraction,
    ExtractRequest,
    ExtractResponse,
    FieldDiffOut,
    Issue,
    MatchOut,
    PdfTextOut,
    ReviewOut,
    ReviewRequest,
    SubmissionDetail,
    SubmissionOut,
)
from .store import (
    AlreadyCheckedError,
    InvalidReviewError,
    NotCheckedError,
    SubmissionNotFoundError,
    attach_run,
    get_submission,
    list_reviews,
    new_submission_id,
    recheck,
    review,
    save_check,
)
from .validation import has_errors, validate_extraction

router = APIRouter(prefix="/expenses", tags=["Bài 03 - Expenses"])

SAMPLE_PDF = SAMPLES_DIR / TASK / "sunrise-hotel.pdf"


@router.get("/samples")
def list_samples():
    """
    Chứng từ mẫu để chạy thử ở chế độ mock (không tốn tiền gọi model).

    Returns:
        list[dict]: Mỗi mẫu gồm label, note, text (văn bản đã OCR).
    """
    return [{"label": s["label"], "note": s.get("note", ""), "text": s["input"]} for s in load_samples(TASK)]


@router.get("/sample-pdf")
def sample_pdf():
    """PDF mẫu có lớp chữ (khách sạn trả bằng USD), cùng nội dung với "Mẫu 7"."""
    return FileResponse(SAMPLE_PDF, media_type="application/pdf", filename=SAMPLE_PDF.name)


@router.post("/pdf-text", response_model=PdfTextOut)
def pdf_text(file: UploadFile = File(..., description="PDF có sẵn lớp chữ, tối đa 5 MB")):
    """
    Lấy chữ từ PDF. Chưa gọi GenAI: người dùng xem lại văn bản rồi mới bấm cho GenAI đọc.

    Raises:
        HTTPException: 400 nếu không phải PDF, PDF hỏng, có mật khẩu, quá lớn,
            hoặc không có lớp chữ (ảnh scan: cần OCR trước).

    Returns:
        PdfTextOut: Tên file và văn bản.
    """
    data = file.file.read(MAX_PDF_BYTES + 1)  # đọc dư 1 byte để biết file có quá lớn không
    try:
        text = pdf_to_text(data)
    except PdfTextError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PdfTextOut(filename=file.filename, text=text)


@router.post("/extract", response_model=ExtractResponse)
def extract(
    payload: ExtractRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Cấp mã chứng từ, cho GenAI đọc văn bản, code kiểm tra kết quả.

    Args:
        payload (ExtractRequest): Văn bản chứng từ (đã OCR hoặc lấy từ PDF).

    Returns:
        ExtractResponse: Mã chứng từ, JSON GenAI trả, bản đã kiểm tra, lỗi/cảnh báo, run_id.
    """
    # Mã chứng từ do backend cấp, không để GenAI hay trình duyệt tự đặt
    submission_id = new_submission_id(db)
    response, result = extract_expense(payload, submission_id, settings, today(tz))

    run = create_run(
        db,
        module=TASK,
        raw_input={**payload.model_dump(mode="json"), "submission_id": submission_id},
        llm_result=result,
        issues=[i.model_dump() for i in response.issues],
    )
    attach_run(db, submission_id, run)
    response.run_id = run.id
    return response


@router.post("/check", response_model=CheckOut)
def check(
    payload: CheckRequest,
    request: Request,
    db: Session = Depends(get_db),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Lưu bản người dùng đã xem/sửa và tìm chứng từ cũ có thể trùng.

    Args:
        payload (CheckRequest): run_id và bản đã sửa.

    Raises:
        HTTPException: 404 không thấy run; 409 đã kiểm tra rồi; 400 còn lỗi
            (tiền âm, loại tiền chưa hỗ trợ, ngày ở tương lai, sai mã chứng từ...).

    Returns:
        CheckOut: Trạng thái, lý do, các chứng từ cũ trùng và ô khác nhau.
    """
    run = get_open_run(db, payload.run_id)
    parsed, issues = check_expense(payload.expense, run.raw_input["submission_id"], tz)

    with request.app.state.expense_lock:
        try:
            result = save_check(db, request.app.state.expense_index, parsed, run)
        except AlreadyCheckedError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ExpenseError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return to_check_out(db, parsed.submission_id, result, issues)


@router.post("/review", response_model=ReviewOut)
def review_submission(
    payload: ReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Người duyệt xác nhận trùng, bỏ nghi vấn, hoặc sửa thông tin rồi kiểm tra trùng lại.

    Sửa mã biên nhận (hay bất kỳ ô nào) thì phải dùng action "recheck": hệ thống kiểm tra lại
    trên bảng tra cứu mới nhất, không giữ kết luận cũ.

    Raises:
        HTTPException: 404 không thấy chứng từ; 409 chưa kiểm tra hoặc không có gì để duyệt;
            400 dữ liệu sửa còn lỗi, hoặc gửi kèm bản sửa khi không phải "recheck".

    Returns:
        ReviewOut: Chứng từ sau khi duyệt, và kết quả kiểm tra lại (nếu có).
    """
    if payload.action != "recheck" and payload.expense is not None:
        raise HTTPException(status_code=400, detail="Đã sửa thông tin chứng từ thì phải chọn kiểm tra lại (recheck)")
    if payload.action == "recheck" and payload.expense is None:
        raise HTTPException(status_code=400, detail="Kiểm tra lại cần gửi kèm thông tin chứng từ đã sửa")

    result, issues = None, []
    with request.app.state.expense_lock:
        try:
            if payload.action == "recheck":
                parsed, issues = check_expense(payload.expense, payload.submission_id, tz)
                result = recheck(db, request.app.state.expense_index, parsed, payload.note)
            else:
                review(db, payload.submission_id, payload.action, payload.note)
        except SubmissionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (NotCheckedError, InvalidReviewError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ExpenseError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    row = get_submission(db, payload.submission_id)
    return ReviewOut(
        submission=to_submission_out(row),
        result=to_check_out(db, payload.submission_id, result, issues) if result else None,
    )


@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """Các chứng từ đã kiểm tra trùng, mới nhất trước."""
    rows = db.scalars(
        select(ExpenseSubmission)
        .where(ExpenseSubmission.status == "checked")
        .order_by(ExpenseSubmission.checked_at.desc(), ExpenseSubmission.id.desc())
        .limit(limit)
    )
    return [to_submission_out(row) for row in rows]


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def submission_detail(submission_id: str, db: Session = Depends(get_db)):
    """Một chứng từ kèm nhật ký duyệt (bản trước và sau mỗi lần sửa)."""
    row = get_submission(db, submission_id)
    if row is None or row.status != "checked":
        raise HTTPException(status_code=404, detail=f"Không có chứng từ {submission_id} đã kiểm tra")
    reviews = [
        {
            "action": r.action, "before": r.before, "after": r.after, "result": r.result,
            "note": r.note, "created_at": as_utc(r.created_at).isoformat(),
        }
        for r in list_reviews(db, submission_id)
    ]
    return SubmissionDetail(submission=to_submission_out(row), reviews=reviews)


# --- Hàm phụ ----------------------------------------------------------------

def today(tz: ZoneInfo) -> date:
    """Ngày hôm nay theo múi giờ app (để chặn ngày giao dịch ở tương lai)."""
    return datetime.now(tz).date()


def get_open_run(db: Session, run_id: int) -> Run:
    """
    Lấy lần extract còn chờ kiểm tra trùng.

    Raises:
        HTTPException: 404 không thấy; 409 đã kiểm tra rồi.
    """
    run = db.get(Run, run_id)
    if run is None or run.module != TASK:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần extract này")
    if run.status != "extracted":
        raise HTTPException(status_code=409, detail=f"Chứng từ này đã được kiểm tra (trạng thái: {run.status})")
    return run


def check_expense(edited: ExpenseExtraction, submission_id: str, tz: ZoneInfo) -> tuple[ExpenseExtraction, list[Issue]]:
    """
    Kiểm tra lại bản người dùng sửa (lớp 2). Lớp 1 FastAPI đã làm: sai khuôn -> 422.

    Mã chứng từ là dữ liệu hệ thống: so với mã đã cấp, không tin trình duyệt.

    Raises:
        HTTPException: 400 kèm danh sách lỗi. App không trả "chưa trùng" khi dữ liệu chưa đủ tin cậy.

    Returns:
        tuple[ExpenseExtraction, list[Issue]]: Bản đã kiểm tra và các cảnh báo (không chặn).
    """
    parsed, issues = validate_extraction(edited.model_dump(mode="json"), submission_id, today(tz))
    if parsed is None or has_errors(issues):
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in issues])
    return parsed, issues


def to_submission_out(row: ExpenseSubmission) -> SubmissionOut:
    """Đổi dòng trong database sang dạng trả về của API."""
    return SubmissionOut(
        submission_id=row.submission_id,
        vendor=row.vendor,
        invoice_number=row.invoice_number,
        transaction_date=row.transaction_date,
        amount_minor=row.amount_minor,
        currency=row.currency,
        receipt_id=row.receipt_id,
        amount_text=format_amount(row.amount_minor, row.currency),
        check_status=row.check_status,
        matched_ids=row.matched_ids or [],
        review_status=row.review_status,
        checked_at=as_utc(row.checked_at) if row.checked_at else None,
        reviewed_at=as_utc(row.reviewed_at) if row.reviewed_at else None,
    )


def to_check_out(db: Session, submission_id: str, result: CheckResult, warnings: list[Issue]) -> CheckOut:
    """Kết quả thuật toán + thông tin đầy đủ của các chứng từ cũ, để hiện cặp nghi trùng cạnh nhau."""
    ids = [m.submission_id for m in result.matches]
    previous = {
        row.submission_id: row
        for row in db.scalars(select(ExpenseSubmission).where(ExpenseSubmission.submission_id.in_(ids)))
    } if ids else {}
    current = get_submission(db, submission_id)
    return CheckOut(
        submission_id=current.submission_id,
        status=result.status,
        reason=result.reason,
        keys=[list(key) for key in result.keys],
        matches=[
            MatchOut(
                submission_id=m.submission_id,
                matched_by=m.matched_by,
                differences=[FieldDiffOut(field=d.field, current=d.current, previous=d.previous, note=d.note) for d in m.differences],
                previous=to_submission_out(previous[m.submission_id]),
            )
            for m in result.matches
        ],
        review_status=current.review_status,
        warnings=warnings,
    )
