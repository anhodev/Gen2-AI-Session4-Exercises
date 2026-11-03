"""Test database Bài 04 trên SQLite tạm: lưu mỗi lần tính, đánh số lần tính, cập nhật run."""

import pytest

from app.core.database import create_tables, make_session_factory
from app.core.llm import LLMResult, load_samples
from app.core.runs import create_run
from app.modules.plans.algo import topological_order
from app.modules.plans.entities import PlanOrder
from app.modules.plans.schemas import DependencyIn, PlanOut, TaskIn
from app.modules.plans.store import first_plan, list_orders, result_payload, save_order

SAMPLE = load_samples("plans")[0]  # ví dụ trong đề


@pytest.fixture
def db(tmp_path):
    engine, factory = make_session_factory(f"sqlite:///{tmp_path}/plans.db")
    create_tables(engine)
    with factory() as session:
        yield session
    engine.dispose()


def new_run(db, llm_output=SAMPLE["llm_output"]):
    return create_run(db, "plans", {"text": SAMPLE["input"]}, LLMResult(llm_output, "mock", 0), [])


def plan(*edges) -> PlanOut:
    return PlanOut(
        tasks=[TaskIn(id=f"T0{n}", title=f"Việc {n}") for n in range(1, 5)],
        dependencies=[DependencyIn(before=a, after=b, evidence=f"câu {a}-{b}") for a, b in edges],
    )


def compute(db, run, p: PlanOut) -> PlanOrder:
    result = topological_order([t.id for t in p.tasks], [(d.before, d.after) for d in p.dependencies])
    return save_order(db, run, p, result, [])


def test_each_computation_is_a_new_row(db):
    run, other = new_run(db), new_run(db)
    assert [compute(db, run, plan()).version for _ in range(3)] == [1, 2, 3]
    assert compute(db, other, plan()).version == 1  # mỗi biên bản đếm riêng
    assert [r.version for r in list_orders(db, run.id)] == [3, 2, 1]
    assert len(list_orders(db)) == 4


def test_cycle_payload_has_evidence_and_blocked_titles():
    p = plan(("T01", "T03"), ("T03", "T04"), ("T04", "T01"))
    payload = result_payload(p, topological_order([t.id for t in p.tasks], [(d.before, d.after) for d in p.dependencies]))
    assert payload["steps"] == []
    assert payload["cycle_dependencies"][2] == {"before": "T04", "after": "T01", "evidence": "câu T04-T01"}
    assert [t["title"] for t in payload["blocked"]] == ["Việc 1", "Việc 3", "Việc 4"]


def test_saved_row_and_run_match(db):
    run = new_run(db)
    row = compute(db, run, plan(("T02", "T01")))
    assert row.status == "ordered"
    assert row.dependencies == [{"before": "T02", "after": "T01", "evidence": "câu T02-T01"}]
    assert [s["task"]["id"] for s in row.result["steps"]] == ["T02", "T01", "T03", "T04"]
    assert run.status == "confirmed"
    assert run.result["order"] == ["T02", "T01", "T03", "T04"] and run.result["order_id"] == row.id


def test_run_result_shows_cycle_instead_of_order(db):
    run = new_run(db)
    compute(db, run, plan(("T01", "T02"), ("T02", "T01")))
    assert run.result["cycle"] == ["T01", "T02", "T01"]
    assert "order" not in run.result


def test_first_plan_uses_system_ids(db):
    first = first_plan(new_run(db))
    assert [t["id"] for t in first["tasks"]] == ["T01", "T02", "T03", "T04"]
    assert first_plan(new_run(db, llm_output=None)) == {}  # GenAI lỗi: mọi ô tính là người dùng nhập
