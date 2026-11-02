"""Bài 02 - Đọc/ghi cửa kho và lịch đặt trong database.

Chặn hai người cùng đặt một giờ bằng 2 lớp:
  1. Code: khóa dòng của cửa (SELECT ... FOR UPDATE), kiểm tra lại trùng giờ trên dữ liệu mới nhất, rồi mới ghi.
  2. Database (chỉ PostgreSQL/Supabase): ràng buộc EXCLUDE tự từ chối lịch chồng nhau.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import as_utc, to_utc
from app.core.entities import Run, now_utc
from app.core.runs import mark_confirmed

from . import algo
from .entities import Dock, DockBooking, DockRequest


class DockNotFoundError(algo.DockError):
    pass


class SlotTakenError(algo.DockError):
    """Giờ đã có người đặt (phát hiện lúc xác nhận)."""


class AlreadyBookedError(algo.DockError):
    """Mã yêu cầu này đã được đặt lịch rồi."""


# Dữ liệu mẫu: đúng ví dụ trong đề (ngày 21/10/2026, giờ kho)
SEED_DAY = (2026, 10, 21)
SEED_DOCKS = [("A", "standard"), ("B", "standard"), ("C", "refrigerated")]
SEED_BOOKINGS = [
    ("A", (9, 0), (10, 0)),
    ("A", (10, 0), (11, 0)),
    ("B", (9, 30), (10, 15)),
    ("C", (10, 0), (11, 0)),
]


def seed_docks(db: Session, tz: tzinfo) -> bool:
    """
    Tạo cửa A, B (thường), C (lạnh) và các lịch có sẵn trong đề, nếu bảng docks còn trống.

    Args:
        db (Session): Database session.
        tz (tzinfo): Múi giờ kho.

    Returns:
        bool: True nếu vừa seed, False nếu đã có dữ liệu.
    """
    if db.scalar(select(func.count()).select_from(Dock)):
        return False

    db.add_all(Dock(dock_id=dock_id, dock_type=dock_type) for dock_id, dock_type in SEED_DOCKS)
    for dock_id, (h1, m1), (h2, m2) in SEED_BOOKINGS:
        db.add(DockBooking(
            dock_id=dock_id,
            carrier="Lịch có sẵn",
            start_at=to_utc(datetime(*SEED_DAY, h1, m1, tzinfo=tz)),
            end_at=to_utc(datetime(*SEED_DAY, h2, m2, tzinfo=tz)),
            created_at=now_utc(),
        ))
    db.commit()
    return True


def new_request_id(db: Session) -> str:
    """
    Sinh mã yêu cầu mới: R001, R002... theo số tự tăng của database.

    Returns:
        str: Mã yêu cầu.
    """
    request = DockRequest(created_at=now_utc())
    db.add(request)
    db.flush()  # lấy id tự tăng trước khi commit
    request.request_id = f"R{request.id:03d}"
    db.commit()
    return request.request_id


def attach_run(db: Session, request_id: str, run: Run) -> None:
    """Ghi lại lần extract tương ứng với mã yêu cầu."""
    request = db.scalar(select(DockRequest).where(DockRequest.request_id == request_id))
    if request is not None:
        request.run_id = run.id
        db.commit()


def list_docks(db: Session) -> list[algo.Dock]:
    """Danh sách cửa, sắp theo mã."""
    return [algo.Dock(d.dock_id, d.dock_type) for d in db.scalars(select(Dock).order_by(Dock.dock_id))]


def load_bookings(db: Session, start: datetime, end: datetime, tz: tzinfo) -> list[algo.Booking]:
    """
    Đọc các lịch giao với khoảng [start, end), ví dụ cả một ngày.

    Args:
        db (Session): Database session.
        start (datetime): Đầu khoảng, có múi giờ.
        end (datetime): Cuối khoảng, có múi giờ.
        tz (tzinfo): Múi giờ kho, để trả giờ dễ đọc.

    Returns:
        list[algo.Booking]: Lịch đã đặt, giờ theo múi giờ kho.
    """
    return [
        algo.Booking(r.dock_id, as_utc(r.start_at).astimezone(tz), as_utc(r.end_at).astimezone(tz), r.request_id)
        for r in load_booking_rows(db, start, end)
    ]


def load_booking_rows(db: Session, start: datetime, end: datetime) -> list[DockBooking]:
    """Bản ghi lịch giao với khoảng [start, end), đủ cột (carrier...) để hiển thị lịch."""
    return list(db.scalars(
        select(DockBooking)
        .where(DockBooking.start_at < to_utc(end), DockBooking.end_at > to_utc(start))
        .order_by(DockBooking.dock_id, DockBooking.start_at)
    ))


def create_booking(
    db: Session,
    request_id: str,
    dock_id: str,
    dock_type: str,
    start: datetime,
    end: datetime,
    carrier: str | None = None,
    run: Run | None = None,
    user_output: dict | None = None,
    cleanup: timedelta = timedelta(0),
    baseline: dict | None = None,
) -> DockBooking:
    """
    Đặt lịch sau khi kiểm tra lại trên dữ liệu mới nhất.

    Args:
        db (Session): Database session.
        request_id (str): Mã yêu cầu.
        dock_id (str): Cửa muốn đặt.
        dock_type (str): Loại cửa yêu cầu cần (phải khớp loại của cửa).
        start (datetime): Giờ bắt đầu, có múi giờ.
        end (datetime): Giờ kết thúc, có múi giờ.
        carrier (str | None, optional): Nhà vận chuyển.
        run (Run | None, optional): Lần extract tương ứng, để ghi kết quả vào lịch sử.
        user_output (dict | None, optional): Bản người dùng đã sửa, lưu vào lịch sử.
        cleanup (timedelta, optional): Thời gian dọn cửa giữa hai xe. Defaults to 0.
        baseline (dict | None, optional): Bản GenAI đã chuẩn hóa, để tìm ô người dùng đã sửa.

    Raises:
        InvalidIntervalError: Giờ kết thúc không sau giờ bắt đầu.
        DockNotFoundError: Cửa không tồn tại hoặc sai loại.
        AlreadyBookedError: Mã yêu cầu đã được đặt.
        SlotTakenError: Giờ này vừa có người khác đặt.

    Returns:
        DockBooking: Lịch vừa lưu.
    """
    slot = algo.Interval(start, end)  # kiểm tra end > start và có múi giờ

    # 1. Khóa dòng của cửa: request khác muốn đặt cùng cửa phải chờ tới khi commit xong
    dock = db.scalar(select(Dock).where(Dock.dock_id == dock_id).with_for_update())
    if dock is None or dock.dock_type != dock_type:
        db.rollback()  # nhả khóa
        detail = f"Không có cửa '{dock_id}'" if dock is None else f"Cửa {dock_id} là cửa {dock.dock_type}, yêu cầu cần cửa {dock_type}"
        raise DockNotFoundError(detail)

    if db.scalar(select(DockBooking).where(DockBooking.request_id == request_id)) is not None:
        db.rollback()
        raise AlreadyBookedError(f"Yêu cầu {request_id} đã được đặt lịch")

    # 2. Kiểm tra lại trùng giờ trên dữ liệu mới nhất. [start, end): chạm nhau không tính là trùng.
    #    Có thời gian dọn cửa thì lịch cũ kéo dài thêm cleanup, lịch mới cũng vậy.
    conflict = db.scalar(
        select(DockBooking)
        .where(
            DockBooking.dock_id == dock_id,
            DockBooking.start_at < to_utc(slot.end + cleanup),
            DockBooking.end_at > to_utc(slot.start - cleanup),
        )
        .limit(1)
    )
    if conflict is not None:
        db.rollback()  # nhả khóa
        raise SlotTakenError(
            f"Cửa {dock_id} đã có lịch {as_utc(conflict.start_at).astimezone(start.tzinfo):%H:%M}"
            f"-{as_utc(conflict.end_at).astimezone(start.tzinfo):%H:%M}, trùng với giờ bạn chọn"
        )

    # 3. Ghi lịch, đánh dấu yêu cầu đã đặt, ghi kết quả vào lịch sử: cùng một lần commit
    booking = DockBooking(
        dock_id=dock_id,
        request_id=request_id,
        run_id=run.id if run else None,
        carrier=carrier,
        start_at=to_utc(slot.start),
        end_at=to_utc(slot.end),
        created_at=now_utc(),
    )
    db.add(booking)
    request = db.scalar(select(DockRequest).where(DockRequest.request_id == request_id))
    if request is not None:
        request.status = "booked"
    if run is not None:
        tz = start.tzinfo
        mark_confirmed(run, user_output or {}, result={
            "dock_id": dock_id,
            "start": slot.start.astimezone(tz).isoformat(),
            "end": slot.end.astimezone(tz).isoformat(),
        }, baseline=baseline)

    try:
        db.commit()
    except IntegrityError as exc:
        # Lớp 2: database (EXCLUDE trên Supabase, hoặc unique request_id) từ chối
        db.rollback()
        raise SlotTakenError(f"Cửa {dock_id} vừa có người đặt trùng giờ, hãy tìm giờ lại") from exc

    db.refresh(booking)
    return booking

