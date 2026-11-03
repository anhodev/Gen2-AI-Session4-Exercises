"""Bài 04 - Lưu mỗi lần tính thứ tự và đọc lại lịch sử.

Mỗi lần tính ghi 2 nơi trong cùng một commit:
  - plan_orders: một dòng mới (việc, quan hệ đã xác nhận, kết quả). Không ghi đè lần trước.
  - runs: bản người dùng sửa và kết quả của lần tính mới nhất, để xem cạnh JSON GenAI trả.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.entities import Run, now_utc
from app.core.runs import mark_confirmed

from .algo import OrderResult, PlanError, explain_step
from .entities import PlanOrder
from .schemas import Issue, PlanOut
from .validation import validate_extraction


class OrderConflictError(PlanError):
    """Biên bản vừa được tính ở một request khác cùng lúc (trùng số lần tính)."""


def result_payload(plan: PlanOut, result: OrderResult) -> dict:
    """
    Kết quả thuật toán, kèm tên việc và câu bằng chứng, để lưu và trả về mà không cần tính lại.

    Returns:
        dict: status, reason, steps, stages, cycle, cycle_dependencies, blocked (dạng JSON).
    """
    tasks = {t.id: t.model_dump() for t in plan.tasks}
    evidence = {(d.before, d.after): d.evidence for d in plan.dependencies}
    return {
        "status": result.status,
        "reason": result.reason,
        "steps": [
            {"position": n, "task": tasks[s.task_id], "waits_for": s.waits_for, "reason": explain_step(s), "stage": s.stage}
            for n, s in enumerate(result.steps, start=1)
        ],
        "stages": [{"stage": n, "tasks": [tasks[t] for t in group]} for n, group in enumerate(result.stages, start=1)],
        "cycle": result.cycle,
        "cycle_dependencies": [
            {"before": a, "after": b, "evidence": evidence[(a, b)]} for a, b in result.cycle_edges
        ],
        "blocked": [tasks[t] for t in result.blocked],
    }


def first_plan(run: Run) -> dict:
    """
    Bản GenAI trả, đã đổi sang mã T01…: dùng để so xem người dùng sửa những gì.
    So thẳng với llm_output thì mọi ô đều khác vì GenAI dùng mã tạm A, B….
    """
    plan, _ = validate_extraction(run.llm_output, run.raw_input["text"])
    return plan.model_dump() if plan else {}


def save_order(db: Session, run: Run, plan: PlanOut, result: OrderResult, warnings: list[Issue]) -> PlanOrder:
    """
    Lưu một lần tính thứ tự (kể cả khi có vòng) và cập nhật run.

    Args:
        db (Session): Database session.
        run (Run): Lần extract của biên bản này.
        plan (PlanOut): Bản đã kiểm tra, mã T01…, đã bỏ quan hệ ghi lặp.
        result (OrderResult): Kết quả thuật toán.
        warnings (list[Issue]): Cảnh báo lúc kiểm tra.

    Returns:
        PlanOrder: Dòng vừa lưu.

    Raises:
        OrderConflictError: Một request khác vừa lưu lần tính cùng số. Không lưu gì, run giữ nguyên.
    """
    version = db.scalar(select(func.count()).where(PlanOrder.run_id == run.id)) + 1
    payload = result_payload(plan, result)
    row = PlanOrder(
        run_id=run.id,
        version=version,
        tasks=[t.model_dump() for t in plan.tasks],
        dependencies=[d.model_dump() for d in plan.dependencies],
        status=result.status,
        result=payload,
        warnings=[w.model_dump() for w in warnings],
        created_at=now_utc(),
    )
    summary = {"version": version, "status": result.status, "reason": result.reason}
    if result.order:
        summary["order"] = result.order
        summary["stages"] = result.stages
    else:
        summary["cycle"] = result.cycle
    baseline = first_plan(run)

    try:
        db.add(row)
        db.flush()  # lấy id để ghi vào run; trùng số lần tính thì database báo lỗi ngay ở đây
        mark_confirmed(run, plan.model_dump(), {"order_id": row.id, **summary}, baseline=baseline)
        db.commit()
    except IntegrityError as exc:
        # Lớp database (unique run_id + version) từ chối: bỏ cả dòng mới lẫn thay đổi trên run
        db.rollback()
        raise OrderConflictError("Biên bản này vừa được tính ở một request khác. Hãy bấm tính lại.") from exc
    db.refresh(row)
    return row


def get_order(db: Session, order_id: int) -> PlanOrder | None:
    return db.get(PlanOrder, order_id)


def list_orders(db: Session, run_id: int | None = None, limit: int = 20) -> list[PlanOrder]:
    """Các lần tính, mới nhất trước. Truyền run_id để xem các lần tính của một biên bản."""
    query = select(PlanOrder).order_by(PlanOrder.id.desc()).limit(limit)
    if run_id is not None:
        query = query.where(PlanOrder.run_id == run_id)
    return list(db.scalars(query))
