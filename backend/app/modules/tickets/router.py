"""Bài 01 - API hàng đợi ticket.

  POST /tickets/extract   Backend cấp mã ticket, GenAI đọc tin nhắn, code kiểm tra, lưu lịch sử. CHƯA vào hàng đợi.
  POST /tickets/confirm   Người dùng xác nhận bản đã sửa -> code tính deadline -> vào hàng đợi.
  GET  /tickets/queue     Xem top K, không làm thay đổi hàng đợi.
  POST /tickets/next      Lấy ticket kế tiếp ra xử lý.

Endpoint viết bằng `def` (không `async def`): gọi GenAI và database là thao tác
chặn (blocking), FastAPI sẽ chạy `def` trong thread riêng nên server không bị đứng.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.dependencies import get_app_settings, get_db, get_display_tz
from app.core.entities import Run
from app.core.llm import load_samples
from app.core.runs import create_run

from .algo import SLA, DuplicateTicketError, QueuedTicket, TicketError
from .entities import Ticket
from .extractor import TASK, extract_ticket
from .schemas import (
    ConfirmRequest,
    ConfirmResponse,
    ExtractRequest,
    ExtractResponse,
    QueueItem,
    QueueResponse,
)
from .store import attach_run, mark_done, new_ticket_id, save_ticket
from .validation import has_errors, validate_extraction

router = APIRouter(prefix="/tickets", tags=["Bài 01 - Tickets"])


@router.get("/samples")
def list_samples():
    """
    Tin nhắn mẫu để chạy thử ở chế độ mock (không tốn tiền gọi model).

    Returns:
        list[dict]: Mỗi mẫu gồm label, note, received_at, message.
    """
    return [
        {
            "label": s["label"],
            "note": s.get("note", ""),
            "received_at": s["received_at"],
            "message": s["input"],
        }
        for s in load_samples(TASK)
    ]


@router.post("/extract", response_model=ExtractResponse)
def extract(
    payload: ExtractRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """
    Cấp mã ticket, cho GenAI đọc tin nhắn và điền mẫu JSON, code kiểm tra kết quả.

    Args:
        payload (ExtractRequest): Giờ nhận (có múi giờ) và tin nhắn.

    Returns:
        ExtractResponse: Mã ticket, JSON GenAI trả về, danh sách lỗi/cảnh báo, run_id để xác nhận.
    """
    # Mã ticket do backend cấp, không nhận từ trình duyệt và không để GenAI tự đặt
    ticket_id = new_ticket_id(db)
    response, result = extract_ticket(payload, ticket_id, settings)

    # Lưu nội dung gốc, JSON GenAI trả, lỗi/cảnh báo và thời gian xử lý
    run = create_run(
        db,
        module=TASK,
        raw_input={**payload.model_dump(mode="json"), "ticket_id": ticket_id},
        llm_result=result,
        issues=[i.model_dump() for i in response.issues],
    )
    attach_run(db, ticket_id, run)
    response.run_id = run.id
    return response


@router.post("/confirm", response_model=ConfirmResponse)
def confirm(
    payload: ConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Người dùng xác nhận bản đã xem/sửa. Code tính deadline rồi đưa vào hàng đợi.

    Args:
        payload (ConfirmRequest): run_id của lần extract và bản người dùng đã sửa.

    Raises:
        HTTPException: 404 không thấy run; 409 run đã xử lý hoặc mã trùng; 400 dữ liệu sai.

    Returns:
        ConfirmResponse: Deadline đã tính và các ô người dùng đã sửa.
    """
    # Tìm lần extract tương ứng
    run = db.get(Run, payload.run_id)
    if run is None or run.module != TASK:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần extract này")
    if run.status != "extracted":
        raise HTTPException(status_code=409, detail=f"Lần extract này đã được xử lý (trạng thái: {run.status})")

    # Mã ticket và giờ nhận là dữ liệu hệ thống: lấy từ lần extract, không lấy từ trình duyệt
    system_ticket_id = run.raw_input["ticket_id"]
    # Đổi sang múi giờ hiển thị để mọi ticket trong heap ghi lý do cùng một múi giờ
    received_at = datetime.fromisoformat(run.raw_input["received_at"]).astimezone(tz)

    # Kiểm tra lại bản người dùng sửa (lớp 2). Lớp 1 FastAPI đã làm: sai khuôn -> 422.
    _, issues = validate_extraction(payload.ticket.model_dump(), expected_ticket_id=system_ticket_id)
    if has_errors(issues):
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in issues])

    # Mã ticket người dùng không sửa được: GenAI trả mã sai thì hệ thống thay,
    # nên khi tìm ô đã sửa, coi như GenAI đã trả đúng mã hệ thống
    baseline = {**run.llm_output, "ticket_id": system_ticket_id} if isinstance(run.llm_output, dict) else None

    # Lưu database + thêm vào heap trong cùng một khóa
    with request.app.state.ticket_lock:
        try:
            queued = save_ticket(db, payload.ticket, received_at, run, baseline=baseline)
        except DuplicateTicketError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except TicketError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        request.app.state.ticket_queue.add(queued)

    return ConfirmResponse(
        ticket_id=queued.ticket_id,
        severity=payload.ticket.severity,
        received_at=queued.received_at.astimezone(tz),
        deadline_at=queued.deadline_at.astimezone(tz),
        sla_minutes=int(SLA[queued.severity].total_seconds() // 60),
        edited_fields=run.result["edited_fields"],
        warnings=issues,
    )


@router.get("/queue", response_model=QueueResponse)
def get_queue(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Xem top K ticket đầu hàng đợi kèm lý do xếp hạng. KHÔNG lấy ticket ra.

    Args:
        limit (int, optional): Số ticket muốn xem (1-100). Defaults to 10.

    Returns:
        QueueResponse: Tổng số ticket đang chờ và top K.
    """
    queue = request.app.state.ticket_queue
    with request.app.state.ticket_lock:
        rows = queue.ranked_view(limit)
        total = len(queue)

    # Heap chỉ giữ thông tin để xếp hạng; intent, product... đọc thêm từ database
    ids = [row["ticket"].ticket_id for row in rows]
    records = {t.ticket_id: t for t in db.scalars(select(Ticket).where(Ticket.ticket_id.in_(ids)))}

    items = [to_queue_item(row, records[row["ticket"].ticket_id], tz) for row in rows]
    return QueueResponse(total=total, items=items)


@router.post("/next", response_model=QueueItem)
def next_ticket(
    request: Request,
    db: Session = Depends(get_db),
    tz: ZoneInfo = Depends(get_display_tz),
):
    """
    Lấy ticket cần xử lý tiếp theo ra khỏi hàng đợi.

    Raises:
        HTTPException: 404 nếu hàng đợi trống.

    Returns:
        QueueItem: Ticket vừa lấy ra, kèm lý do nó đứng đầu.
    """
    queue = request.app.state.ticket_queue
    with request.app.state.ticket_lock:
        top = queue.ranked_view(1) if len(queue) else []
        if not top:
            raise HTTPException(status_code=404, detail="Hàng đợi trống")
        ticket = queue.pop_next()
        record = mark_done(db, ticket.ticket_id)

    return to_queue_item(top[0], record, tz)


def to_queue_item(row: dict, record: Ticket, tz: ZoneInfo) -> QueueItem:
    """
    Ghép dữ liệu xếp hạng (từ heap) và chi tiết ticket (từ database).

    Args:
        row (dict): Một dòng của ranked_view: position, ticket, reason.
        record (Ticket): Bản ghi ticket trong database.
        tz (ZoneInfo): Múi giờ hiển thị.

    Returns:
        QueueItem: Một dòng trong bảng hàng đợi.
    """
    ticket: QueuedTicket = row["ticket"]
    return QueueItem(
        position=row["position"],
        ticket_id=ticket.ticket_id,
        severity=ticket.severity,
        intent=record.intent,
        product=record.product,
        summary=record.summary,
        received_at=ticket.received_at.astimezone(tz),
        deadline_at=ticket.deadline_at.astimezone(tz),
        reason=row["reason"],
    )
