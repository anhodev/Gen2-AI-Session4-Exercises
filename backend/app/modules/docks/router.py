"""Bài 02 - API giờ nhận xe ở cửa kho.

  GET  /docks/samples            Email mẫu để chạy thử ở chế độ mock.
  POST /docks/requests/extract   Backend sinh mã yêu cầu, GenAI đọc email, code kiểm tra. CHƯA tìm giờ.
  POST /docks/slots/suggest      Người dùng đã xem/sửa -> thuật toán tìm cửa và giờ sớm nhất. CHƯA đặt.
  POST /docks/bookings/confirm   Đặt cửa và giờ đã chọn. Kiểm tra lại trên dữ liệu mới nhất; trùng -> 409 kèm gợi ý mới.
  GET  /docks/schedule?date=     Lịch một ngày của tất cả cửa.

Thuật toán không tin vào gợi ý cũ: lúc xác nhận, lịch có thể đã thay đổi (người khác vừa đặt).
Thời gian dọn cửa (DOCK_CLEANUP_MINUTES) được tính ở cả lúc gợi ý và lúc xác nhận.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import as_utc
from app.core.dependencies import get_app_settings, get_db, get_display_tz
from app.core.entities import Run
from app.core.llm import load_samples
from app.core.runs import create_run
from app.core.timeutil import gmt_label, parse_hhmm, to_app_tz

from .algo import DockError, SlotResult, suggest_slot, working_window
from .extractor import TASK, check_extraction, extract_dock_request
from .schemas import (
    BookingOut,
    BusyBlock,
    ConfirmRequest,
    DockCheckOut,
    DockRequestExtraction,
    DockSchedule,
    ExtractRequest,
    ExtractResponse,
    ScheduleBooking,
    ScheduleOut,
    SlotOut,
    SuggestRequest,
)
from .store import (
    AlreadyBookedError,
    SlotTakenError,
    attach_run,
    create_booking,
    list_docks,
    load_booking_rows,
    load_bookings,
    new_request_id,
)
from .validation import has_errors, to_slot_request

router = APIRouter(prefix="/docks", tags=["Bài 02 - Docks"])


@router.get("/samples")
def list_samples():
    """
    Email mẫu để chạy thử ở chế độ mock (không tốn tiền gọi model).

    Returns:
        list[dict]: Mỗi mẫu gồm label, note, received_at, email.
    """
    return [
        {"label": s["label"], "note": s.get("note", ""), "received_at": s["received_at"], "email": s["input"]}
        for s in load_samples(TASK)
    ]


@router.post("/requests/extract", response_model=ExtractResponse)
def extract(
    payload: ExtractRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """
    Sinh mã yêu cầu, cho GenAI đọc email, code kiểm tra kết quả.

    Args:
        payload (ExtractRequest): Email và thời điểm nhận (nếu có).

    Returns:
        ExtractResponse: Mã yêu cầu, JSON GenAI trả, bản đã kiểm tra, lỗi/cảnh báo, run_id.
    """
    # Mã yêu cầu do backend sinh, không để GenAI tự đặt
    request_id = new_request_id(db)
    response, result = extract_dock_request(payload, request_id, settings)

    run = create_run(
        db,
        module=TASK,
        raw_input={**payload.model_dump(mode="json"), "request_id": request_id},
        llm_result=result,
        issues=[i.model_dump() for i in response.issues],
    )
    attach_run(db, request_id, run)
    response.run_id = run.id
    return response


@router.post("/slots/suggest", response_model=SlotOut)
def suggest(
    payload: SuggestRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Tìm cửa và giờ sớm nhất cho bản người dùng đã xem/sửa. Chỉ gợi ý, chưa đặt.

    Args:
        payload (SuggestRequest): run_id và bản đã sửa.

    Raises:
        HTTPException: 404 không thấy run; 409 đã đặt rồi; 400 còn lỗi (vd thiếu thời lượng).

    Returns:
        SlotOut: Cửa, giờ, lý do và tình trạng từng cửa. status "no_slot" nếu hết chỗ.
    """
    run = get_open_run(db, payload.run_id)
    parsed = check_request(payload.request, run, settings, tz)
    return to_slot_out(find_slot(db, parsed, settings, tz), settings.dock_cleanup_minutes)


@router.post("/bookings/confirm", response_model=BookingOut)
def confirm(
    payload: ConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Đặt cửa và giờ đã chọn, sau khi kiểm tra lại trên dữ liệu mới nhất.

    Args:
        payload (ConfirmRequest): run_id, bản đã sửa, cửa và giờ bắt đầu đã chọn.

    Raises:
        HTTPException: 404 không thấy run; 400 dữ liệu sai hoặc giờ ngoài giờ mở cửa;
            409 đã đặt rồi, hoặc giờ vừa bị người khác đặt (detail kèm gợi ý mới).

    Returns:
        BookingOut: Lịch vừa đặt và các ô người dùng đã sửa.
    """
    run = get_open_run(db, payload.run_id)
    parsed = check_request(payload.request, run, settings, tz)
    request_id = run.raw_input["request_id"]

    start = to_app_tz(payload.start, tz)
    end = start + to_slot_request(parsed).duration
    check_within_hours(start, end, parsed.earliest_start, settings, tz)

    # Bản GenAI đã chuẩn hóa giờ, để so sánh tìm ô người dùng đã sửa
    llm_parsed, _ = check_extraction(run.llm_output, request_id, settings, tz)
    baseline = llm_parsed.model_dump(mode="json") if llm_parsed else None

    # Khóa trong app: SQLite không có SELECT ... FOR UPDATE, nên cần thêm khóa này
    with request.app.state.dock_lock:
        try:
            booking = create_booking(
                db, request_id, payload.dock_id, parsed.dock_type, start, end,
                carrier=parsed.carrier, run=run,
                user_output=parsed.model_dump(mode="json"), baseline=baseline,
                cleanup=cleanup(settings),
            )
        except SlotTakenError as exc:
            # Không đánh dấu run là rejected: người dùng vẫn đặt lại được với gợi ý mới
            suggestion = to_slot_out(find_slot(db, parsed, settings, tz), settings.dock_cleanup_minutes)
            raise HTTPException(status_code=409, detail={"message": str(exc), "suggestion": suggestion.model_dump(mode="json")})
        except AlreadyBookedError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except DockError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return BookingOut(
        booking_id=booking.id,
        request_id=request_id,
        dock_id=booking.dock_id,
        carrier=booking.carrier,
        start=as_utc(booking.start_at).astimezone(tz),
        end=as_utc(booking.end_at).astimezone(tz),
        edited_fields=run.result["edited_fields"],
        cleanup_minutes=settings.dock_cleanup_minutes,
    )


@router.get("/schedule", response_model=ScheduleOut)
def schedule(
    day: date | None = Query(None, alias="date", description="YYYY-MM-DD theo giờ kho. Bỏ trống = hôm nay."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Lịch một ngày của tất cả cửa, để vẽ timeline.

    Args:
        day (date | None, optional): Ngày cần xem. Defaults to hôm nay.

    Returns:
        ScheduleOut: Giờ mở/đóng cửa, múi giờ và lịch của từng cửa.
    """
    day = day or datetime.now(tz).date()
    midnight = datetime.combine(day, time(0), tz)
    open_at, close_at = working_window(midnight, tz, *hours(settings))

    rows = load_booking_rows(db, midnight, midnight + timedelta(days=1))
    docks = []
    for dock in list_docks(db):
        bookings = [
            ScheduleBooking(
                request_id=r.request_id,
                carrier=r.carrier,
                start=as_utc(r.start_at).astimezone(tz),
                end=as_utc(r.end_at).astimezone(tz),
            )
            for r in rows if r.dock_id == dock.dock_id
        ]
        docks.append(DockSchedule(dock_id=dock.dock_id, dock_type=dock.dock_type, bookings=bookings))

    return ScheduleOut(
        date=day.isoformat(), timezone=gmt_label(open_at), open_at=open_at, close_at=close_at,
        cleanup_minutes=settings.dock_cleanup_minutes, docks=docks,
    )


# --- Hàm phụ ----------------------------------------------------------------

def hours(settings: Settings) -> tuple[time, time]:
    """Giờ mở và đóng cửa lấy từ cấu hình (DOCK_OPEN, DOCK_CLOSE)."""
    return parse_hhmm(settings.dock_open), parse_hhmm(settings.dock_close)


def cleanup(settings: Settings) -> timedelta:
    """Thời gian dọn cửa sau mỗi xe, lấy từ cấu hình (DOCK_CLEANUP_MINUTES)."""
    return timedelta(minutes=settings.dock_cleanup_minutes)


def get_open_run(db: Session, run_id: int) -> Run:
    """
    Lấy lần extract còn chờ đặt lịch.

    Raises:
        HTTPException: 404 không thấy; 409 đã đặt hoặc đã bị từ chối.
    """
    run = db.get(Run, run_id)
    if run is None or run.module != TASK:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần extract này")
    if run.status != "extracted":
        raise HTTPException(status_code=409, detail=f"Yêu cầu này đã được xử lý (trạng thái: {run.status})")
    return run


def check_request(edited: DockRequestExtraction, run: Run, settings: Settings, tz: ZoneInfo) -> DockRequestExtraction:
    """
    Kiểm tra lại bản người dùng sửa (lớp 2). Lớp 1 FastAPI đã làm: sai khuôn -> 422.

    Mã yêu cầu là dữ liệu hệ thống: so với mã đã lưu trong run, không tin trình duyệt.

    Raises:
        HTTPException: 400 kèm danh sách lỗi nếu còn lỗi (vd thiếu thời lượng).
    """
    parsed, issues = check_extraction(edited.model_dump(), run.raw_input["request_id"], settings, tz)
    if parsed is None or has_errors(issues):
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in issues])
    return parsed


def check_within_hours(start: datetime, end: datetime, earliest: datetime, settings: Settings, tz: ZoneInfo) -> None:
    """
    Giờ đã chọn phải nằm trong giờ mở cửa và không sớm hơn giờ xe đến.

    Raises:
        HTTPException: 400 nếu sai.
    """
    open_at, close_at = working_window(start, tz, *hours(settings))
    if start < earliest:
        raise HTTPException(status_code=400, detail=f"Giờ bắt đầu {start:%H:%M} sớm hơn giờ xe đến {earliest:%H:%M}")
    if start < open_at or end > close_at:
        raise HTTPException(
            status_code=400,
            detail=f"Lịch {start:%H:%M}-{end:%H:%M} nằm ngoài giờ mở cửa {open_at:%H:%M}-{close_at:%H:%M} ({gmt_label(start)})",
        )


def find_slot(db: Session, parsed: DockRequestExtraction, settings: Settings, tz: ZoneInfo) -> SlotResult:
    """
    Chạy thuật toán trên lịch mới nhất của ngày xe đến.

    Raises:
        HTTPException: 400 nếu còn thiếu giờ đến hoặc thời lượng.
    """
    try:
        slot_request = to_slot_request(parsed)
    except DockError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    open_time, close_time = hours(settings)
    open_at, close_at = working_window(slot_request.earliest_start, tz, open_time, close_time)
    # Lùi đầu khoảng thêm thời gian dọn: xe kết thúc ngay trước giờ mở cửa vẫn làm cửa bận lúc mở
    bookings = load_bookings(db, open_at - cleanup(settings), close_at, tz)
    return suggest_slot(slot_request, list_docks(db), bookings, tz, open_time, close_time, cleanup(settings))


def to_slot_out(result: SlotResult, cleanup_minutes: int = 0) -> SlotOut:
    """Đổi kết quả thuật toán sang dạng trả về của API."""
    return SlotOut(
        status=result.status,
        cleanup_minutes=cleanup_minutes,
        dock_id=result.dock_id,
        start=result.start,
        end=result.end,
        reason=result.reason,
        checks=[
            DockCheckOut(
                dock_id=c.dock_id,
                busy=[BusyBlock(start=i.start, end=i.end) for i in c.busy],
                free_from=c.free_from,
            )
            for c in result.checks
        ],
    )
