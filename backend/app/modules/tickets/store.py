"""Bài 01 - Đọc/ghi ticket trong database.

Heap nằm trong bộ nhớ để thêm/lấy nhanh O(log n). Database giữ bản lâu dài:
khi app khởi động lại, load_queue() đọc các ticket còn "queued" và dựng lại heap.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import as_utc, to_utc
from app.core.entities import Run, now_utc
from app.core.runs import mark_confirmed, mark_rejected

from .algo import SLA, DuplicateTicketError, QueuedTicket, TicketQueue, make_ticket
from .entities import Ticket, TicketNumber
from .schemas import TicketExtraction


def new_ticket_id(db: Session) -> str:
    """
    Sinh mã ticket mới: T01, T02... theo số tự tăng của database (quá 99 thì thành T100).

    Bỏ qua mã đã có trong bảng tickets (vd ticket nhập tay từ trước khi backend tự cấp mã).

    Returns:
        str: Mã ticket.
    """
    while True:
        number = TicketNumber(created_at=now_utc())
        db.add(number)
        db.flush()  # lấy id tự tăng trước khi commit
        ticket_id = f"T{number.id:02d}"
        if db.get(Ticket, ticket_id) is None:
            number.ticket_id = ticket_id
            db.commit()
            return ticket_id


def attach_run(db: Session, ticket_id: str, run: Run) -> None:
    """Ghi lại lần extract tương ứng với mã ticket."""
    number = db.scalar(select(TicketNumber).where(TicketNumber.ticket_id == ticket_id))
    if number is not None:
        number.run_id = run.id
        db.commit()


def save_ticket(
    db: Session,
    data: TicketExtraction,
    received_at: datetime,
    run: Run | None = None,
    baseline: dict | None = None,
) -> QueuedTicket:
    """
    Lưu ticket người dùng đã xác nhận và ghi kết quả vào lịch sử.

    Args:
        db (Session): Database session.
        data (TicketExtraction): Bản người dùng đã xem/sửa.
        received_at (datetime): Thời điểm hệ thống nhận tin nhắn, có múi giờ.
        run (Run | None, optional): Lần extract tương ứng. Defaults to None.
        baseline (dict | None, optional): Bản để so tìm ô người dùng đã sửa.
            Defaults to None (so với JSON GenAI trả).

    Raises:
        DuplicateTicketError: Mã ticket đã tồn tại.
        TicketError: Mức khẩn cấp sai hoặc thời gian thiếu múi giờ.

    Returns:
        QueuedTicket: Ticket kèm deadline, sẵn sàng đưa vào heap.
    """
    user_output = data.model_dump()

    # Quy tắc mã trùng: từ chối, không ghi đè
    if db.get(Ticket, data.ticket_id) is not None:
        reject(db, run, user_output, f"ticket_id '{data.ticket_id}' đã tồn tại")

    # Code tự tính deadline (và kiểm tra severity, múi giờ lần nữa)
    queued = make_ticket(data.ticket_id, received_at, data.severity, data.summary)

    db.add(Ticket(
        ticket_id=queued.ticket_id,
        run_id=run.id if run else None,
        intent=data.intent,
        severity=data.severity,
        product=data.product,
        summary=data.summary,
        needs_human_review=data.needs_human_review,
        # Đổi về UTC trước khi lưu (xem database.to_utc)
        received_at=to_utc(queued.received_at),
        deadline_at=to_utc(queued.deadline_at),
        status="queued",
        created_at=now_utc(),
    ))
    if run is not None:
        mark_confirmed(run, user_output, result={
            "deadline_at": queued.deadline_at.isoformat(),
            "sla_minutes": int(SLA[data.severity].total_seconds() // 60),
        }, baseline=baseline)

    # Ghi ticket và lịch sử trong cùng một lần commit
    try:
        db.commit()
    except IntegrityError:
        # Hai người cùng xác nhận một mã cùng lúc: người sau bị từ chối
        db.rollback()
        reject(db, run, user_output, f"ticket_id '{data.ticket_id}' đã tồn tại")

    return queued


def reject(db: Session, run: Run | None, user_output: dict, reason: str) -> None:
    """Ghi lý do từ chối vào lịch sử rồi báo lỗi trùng mã."""
    if run is not None:
        mark_rejected(run, user_output, reason)
        db.commit()
    raise DuplicateTicketError(reason)


def load_queue(db: Session, tz: tzinfo = timezone.utc) -> TicketQueue:
    """
    Dựng lại heap từ các ticket còn trong hàng đợi.

    Args:
        db (Session): Database session.
        tz (tzinfo, optional): Múi giờ hiển thị. Database trả về UTC,
            đổi sang múi giờ này để lý do xếp hạng dễ đọc. Defaults to UTC.

    Returns:
        TicketQueue: Hàng đợi chứa mọi ticket có status="queued".
    """
    queue = TicketQueue()
    for row in db.scalars(select(Ticket).where(Ticket.status == "queued")):
        received_at = as_utc(row.received_at).astimezone(tz)
        queue.add(make_ticket(row.ticket_id, received_at, row.severity, row.summary))
    return queue


def mark_done(db: Session, ticket_id: str) -> Ticket | None:
    """
    Đánh dấu ticket đã được lấy ra xử lý.

    Args:
        db (Session): Database session.
        ticket_id (str): Mã ticket.

    Returns:
        Ticket | None: Bản ghi đã cập nhật, None nếu không tìm thấy.
    """
    row = db.get(Ticket, ticket_id)
    if row is None:
        return None
    row.status = "done"
    row.popped_at = now_utc()
    db.commit()
    return row
