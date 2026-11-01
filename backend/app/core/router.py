"""Endpoint dùng chung: kiểm tra app và xem lịch sử xử lý."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import as_utc
from .dependencies import get_db
from .entities import Run

router = APIRouter(tags=["Chung"])


class RunOut(BaseModel):
    """Một lần xử lý: nội dung gốc -> GenAI -> người dùng sửa -> kết quả."""

    model_config = ConfigDict(from_attributes=True)  # đọc thẳng từ object SQLAlchemy

    id: int
    module: str
    status: str
    raw_input: dict
    llm_output: dict | None
    user_output: dict | None
    result: dict | None
    issues: list | None
    error: str | None
    model: str
    latency_ms: int
    created_at: datetime
    confirmed_at: datetime | None

    @field_validator("created_at", "confirmed_at")
    @classmethod
    def time_must_have_timezone(cls, v):
        # Database lưu UTC; SQLite trả về không có múi giờ -> gắn UTC để UI hiện đúng
        return as_utc(v)


@router.get("/health")
def health(request: Request):
    """
    Kiểm tra app đang chạy và đang dùng model/database nào.

    Returns:
        dict: Trạng thái app.
    """
    settings = request.app.state.settings
    return {
        "status": "ok",
        "llm": "mock" if settings.use_mock else settings.openai_model,
        "database": request.app.state.engine.dialect.name,  # postgresql hoặc sqlite
    }


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    module: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Xem lịch sử xử lý, mới nhất trước.

    Args:
        module (str | None, optional): Lọc theo bài, ví dụ "tickets".
        limit (int, optional): Số dòng tối đa. Defaults to 20.
        db (Session): Database session.

    Returns:
        list[RunOut]: Danh sách lần xử lý.
    """
    query = select(Run).order_by(Run.id.desc()).limit(limit)
    if module:
        query = query.where(Run.module == module)
    return db.scalars(query).all()


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    """
    Xem chi tiết một lần xử lý.

    Raises:
        HTTPException: 404 nếu không tìm thấy.
    """
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần xử lý")
    return run
