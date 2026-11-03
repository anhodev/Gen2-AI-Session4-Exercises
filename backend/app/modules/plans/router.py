"""Bài 04 - API lập thứ tự công việc từ biên bản họp.

  GET  /plans/samples              Biên bản mẫu để chạy thử ở chế độ mock.
  POST /plans/extract              GenAI đọc biên bản, code kiểm tra và cấp mã T01…. CHƯA tính thứ tự.
  POST /plans/validate             Kiểm tra bản người dùng đang sửa, cấp mã cho việc mới thêm. Không lưu.
  POST /plans/order                Tính thứ tự (hoặc báo vòng chờ nhau) và lưu lần tính này.
  GET  /plans/orders               Lịch sử các lần tính, mới nhất trước.
  GET  /plans/orders/{order_id}    Một lần tính, đúng như lúc tính.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import as_utc
from app.core.dependencies import get_app_settings, get_db
from app.core.entities import Run
from app.core.llm import load_samples
from app.core.runs import create_run

from .algo import PlanError, topological_order
from .entities import PlanOrder
from .extractor import TASK, extract_plan
from .schemas import (
    ExtractRequest,
    ExtractResponse,
    Issue,
    OrderOut,
    OrderSummary,
    PlanOut,
    PlanRequest,
    ValidateOut,
)
from .store import get_order, list_orders, save_order
from .validation import has_errors, validate_plan

router = APIRouter(prefix="/plans", tags=["Bài 04 - Plans"])


@router.get("/samples")
def list_samples():
    """
    Biên bản mẫu để chạy thử ở chế độ mock (không tốn tiền gọi model).

    Returns:
        list[dict]: Mỗi mẫu gồm label, note, text.
    """
    return [{"label": s["label"], "note": s.get("note", ""), "text": s["input"]} for s in load_samples(TASK)]


@router.post("/extract", response_model=ExtractResponse)
def extract(
    payload: ExtractRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """
    Cho GenAI đọc biên bản, code kiểm tra kết quả và cấp mã T01….

    Returns:
        ExtractResponse: JSON GenAI trả, bản đã cấp mã, lỗi/cảnh báo, run_id.
    """
    response, result = extract_plan(payload, settings)
    run = create_run(
        db,
        module=TASK,
        raw_input=payload.model_dump(),
        llm_result=result,
        issues=[i.model_dump() for i in response.issues],
    )
    response.run_id = run.id
    return response


@router.post("/validate", response_model=ValidateOut)
def validate(payload: PlanRequest, db: Session = Depends(get_db)):
    """
    Kiểm tra bản người dùng đang sửa. Việc mới thêm (id=null) được cấp mã kế tiếp.

    Giao diện gọi sau mỗi lần thêm việc, để có mã trước khi nối quan hệ với việc đó. Không lưu gì.

    Raises:
        HTTPException: 404 không thấy lần extract.

    Returns:
        ValidateOut: Bản đã cấp mã, lỗi/cảnh báo, và có tính thứ tự được chưa.
    """
    run = get_plan_run(db, payload.run_id)
    plan, issues = validate_plan(payload.plan.model_dump(), run.raw_input["text"])
    return ValidateOut(plan=plan, issues=issues, can_order=plan is not None and not has_errors(issues))


@router.post("/order", response_model=OrderOut)
def order(payload: PlanRequest, db: Session = Depends(get_db)):
    """
    Tính thứ tự công việc từ bản người dùng đã xác nhận, rồi lưu lần tính này.

    Có vòng chờ nhau vẫn trả 200 (status cycle_detected) và vẫn lưu: đó là kết quả để người dùng sửa,
    không phải lỗi của request. Khi có vòng KHÔNG trả nửa danh sách thứ tự.

    Raises:
        HTTPException: 404 không thấy lần extract; 400 còn lỗi (mã lạ, việc tự chờ chính nó…).

    Returns:
        OrderOut: Thứ tự kèm lý do từng việc, hoặc vòng chờ nhau kèm câu bằng chứng của từng quan hệ.
    """
    run = get_plan_run(db, payload.run_id)
    plan, issues = validate_plan(payload.plan.model_dump(), run.raw_input["text"])
    if plan is None or has_errors(issues):
        raise HTTPException(status_code=400, detail=[i.model_dump() for i in issues])

    try:
        result = topological_order([t.id for t in plan.tasks], [(d.before, d.after) for d in plan.dependencies])
    except PlanError as exc:  # validation đã chặn; giữ lại để dữ liệu sai không bao giờ ra thứ tự
        raise HTTPException(status_code=400, detail=str(exc))

    row = save_order(db, run, plan, result, issues)
    return to_order_out(row)


@router.get("/orders", response_model=list[OrderSummary])
def orders(
    run_id: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Lịch sử các lần tính, mới nhất trước. Truyền run_id để xem các lần tính của một biên bản."""
    return [
        OrderSummary(
            order_id=row.id,
            run_id=row.run_id,
            version=row.version,
            status=row.status,
            reason=row.result["reason"],
            task_count=len(row.tasks),
            dependency_count=len(row.dependencies),
            created_at=as_utc(row.created_at),
        )
        for row in list_orders(db, run_id, limit)
    ]


@router.get("/orders/{order_id}", response_model=OrderOut)
def order_detail(order_id: int, db: Session = Depends(get_db)):
    """
    Một lần tính, đúng như lúc tính (đọc từ database, không tính lại).

    Raises:
        HTTPException: 404 nếu không có.
    """
    row = get_order(db, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần tính này")
    return to_order_out(row)


# --- Hàm phụ ----------------------------------------------------------------

def get_plan_run(db: Session, run_id: int) -> Run:
    """
    Lấy lần extract biên bản. Tính lại nhiều lần được, nên không chặn run đã có kết quả.

    Raises:
        HTTPException: 404 không thấy, hoặc run của bài khác.
    """
    run = db.get(Run, run_id)
    if run is None or run.module != TASK:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần extract biên bản này")
    return run


def to_order_out(row: PlanOrder) -> OrderOut:
    """Đổi dòng trong database sang dạng trả về của API."""
    return OrderOut(
        order_id=row.id,
        run_id=row.run_id,
        version=row.version,
        plan=PlanOut(tasks=row.tasks, dependencies=row.dependencies),
        warnings=[Issue(**w) for w in row.warnings],
        created_at=as_utc(row.created_at),
        **row.result,
    )
