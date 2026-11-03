"""Test API Bài 04 bằng TestClient. Mock GenAI + SQLite tạm, không cần mạng."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.llm import load_samples
from app.main import create_app

# Biên bản mẫu, đánh chỉ mục theo số thứ tự: 1 = "Mẫu 1 - ..."
SAMPLES = {i: s for i, s in enumerate(load_samples("plans"), start=1)}


@pytest.fixture
def client(tmp_path):
    # "with" để chạy lifespan: tạo bảng
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/api.db", mock_llm=True))
    with TestClient(app) as c:
        yield c


def extract(client, n: int) -> dict:
    response = client.post("/plans/extract", json={"text": SAMPLES[n]["input"]})
    assert response.status_code == 200, response.text
    return response.json()


def order(client, run_id: int, plan: dict):
    return client.post("/plans/order", json={"run_id": run_id, "plan": plan})


def validate(client, run_id: int, plan: dict):
    return client.post("/plans/validate", json={"run_id": run_id, "plan": plan})


def ids(steps: list[dict]) -> list[str]:
    return [s["task"]["id"] for s in steps]


# --- Ca thường --------------------------------------------------------------

def test_samples_endpoint(client):
    samples = client.get("/plans/samples").json()
    assert len(samples) == len(SAMPLES)
    assert samples[0]["text"] == SAMPLES[1]["input"]


def test_extract_saves_run(client):
    data = extract(client, 1)
    assert [t["id"] for t in data["plan"]["tasks"]] == ["T01", "T02", "T03", "T04"]
    assert data["can_order"] is True
    run = client.get(f"/runs/{data['run_id']}").json()
    assert (run["module"], run["status"]) == ("plans", "extracted")
    assert run["llm_output"]["tasks"][0]["id"] == "A"


def test_order_exercise_example(client):
    data = extract(client, 1)
    response = order(client, data["run_id"], data["plan"])
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["status"], out["version"]) == ("ordered", 1)
    assert ids(out["steps"]) == ["T01", "T02", "T03", "T04"]
    assert out["steps"][0]["task"]["title"] == "Chốt yêu cầu API"
    assert out["steps"][2]["reason"] == "Chờ T01, T02 xong"
    assert out["cycle"] == [] and out["blocked"] == []


def test_cycle_is_reported_with_evidence_and_saved(client):
    data = extract(client, 2)
    out = order(client, data["run_id"], data["plan"]).json()
    assert out["status"] == "cycle_detected"
    assert out["steps"] == []  # không trả nửa danh sách
    assert out["cycle"] == ["T01", "T03", "T04", "T01"]
    assert out["cycle_dependencies"][-1] == {
        "before": "T04", "after": "T01", "evidence": "Product chỉ chốt API sau khi QA kiểm thử xong",
    }
    assert [t["id"] for t in out["blocked"]] == ["T01", "T03", "T04"]
    assert "T01 → T03 → T04 → T01" in out["reason"]
    assert client.get(f"/plans/orders/{out['order_id']}").json() == out


def test_fix_cycle_and_order_again_keeps_both_versions(client):
    # Tự kiểm tra 2: có D -> A thì báo vòng, xóa đi thì xếp lại được
    data = extract(client, 2)
    first = order(client, data["run_id"], data["plan"]).json()
    fixed = {**data["plan"], "dependencies": data["plan"]["dependencies"][:-1]}
    second = order(client, data["run_id"], fixed).json()
    assert (second["status"], second["version"]) == ("ordered", 2)
    assert ids(second["steps"]) == ["T01", "T02", "T03", "T04"]

    history = client.get("/plans/orders", params={"run_id": data["run_id"]}).json()
    assert [(h["version"], h["status"]) for h in history] == [(2, "ordered"), (1, "cycle_detected")]
    assert (history[1]["task_count"], history[1]["dependency_count"]) == (4, 4)
    # Lần đầu vẫn giữ nguyên như lúc tính
    assert client.get(f"/plans/orders/{first['order_id']}").json()["status"] == "cycle_detected"


def test_run_keeps_user_output_and_latest_result(client):
    data = extract(client, 1)
    plan = data["plan"]
    plan["tasks"][3]["owner"] = "QA team"
    out = order(client, data["run_id"], plan).json()
    run = client.get(f"/runs/{data['run_id']}").json()
    assert run["status"] == "confirmed"
    assert run["user_output"]["tasks"][3]["owner"] == "QA team"
    assert run["result"]["order"] == ["T01", "T02", "T03", "T04"]
    assert run["result"]["order_id"] == out["order_id"]
    # So với bản GenAI đã cấp mã, không phải mã tạm A, B: chỉ danh sách việc bị sửa
    assert run["result"]["edited_fields"] == ["tasks"]


def test_same_plan_with_shuffled_json_gives_same_order(client):
    # Tự kiểm tra 1
    data = extract(client, 6)
    plan = data["plan"]
    shuffled = {"tasks": plan["tasks"][::-1], "dependencies": plan["dependencies"][::-1]}
    first = order(client, data["run_id"], plan).json()
    second = order(client, data["run_id"], shuffled).json()
    assert ids(first["steps"]) == ids(second["steps"]) == ["T01", "T02", "T03", "T04", "T05", "T06", "T07"]


def test_no_dependencies_is_ordered_with_warning(client):
    data = extract(client, 3)
    out = order(client, data["run_id"], data["plan"]).json()
    assert out["status"] == "ordered"
    assert [w["field"] for w in out["warnings"]] == ["dependencies"]


# --- Thêm / sửa việc --------------------------------------------------------

def test_validate_gives_new_task_next_id(client):
    data = extract(client, 1)
    plan = data["plan"]
    plan["tasks"].append({"id": None, "title": "Phát hành", "owner": None})
    out = validate(client, data["run_id"], plan).json()
    assert out["plan"]["tasks"][-1]["id"] == "T05"
    assert out["can_order"] is True


def test_added_task_and_dependency_without_evidence(client):
    data = extract(client, 1)
    plan = data["plan"]
    plan["tasks"].append({"id": "T05", "title": "Phát hành", "owner": "Product"})
    plan["dependencies"].append({"before": "T04", "after": "T05", "evidence": None})
    out = order(client, data["run_id"], plan).json()
    assert ids(out["steps"])[-1] == "T05"
    assert out["warnings"] == []  # quan hệ người dùng tự thêm không bị đòi câu bằng chứng


def test_validate_saves_nothing(client):
    data = extract(client, 1)
    validate(client, data["run_id"], data["plan"])
    assert client.get("/plans/orders").json() == []
    assert client.get(f"/runs/{data['run_id']}").json()["status"] == "extracted"


def test_invented_dependency_warning_is_kept(client):
    data = extract(client, 10)
    out = order(client, data["run_id"], data["plan"]).json()
    assert [w["field"] for w in out["warnings"]] == ["dependencies.0.evidence"]
    saved = client.get(f"/plans/orders/{out['order_id']}").json()
    assert saved["warnings"] == out["warnings"]


# --- Dữ liệu sai ------------------------------------------------------------

def test_order_rejects_unknown_task(client):
    data = extract(client, 1)
    plan = data["plan"]
    plan["dependencies"].append({"before": "T04", "after": "T09", "evidence": None})
    response = order(client, data["run_id"], plan)
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "dependencies.3.after"
    assert client.get("/plans/orders").json() == []  # lỗi thì không lưu


def test_order_rejects_self_dependency_from_genai(client):
    data = extract(client, 8)
    assert data["can_order"] is False
    response = order(client, data["run_id"], data["plan"])
    assert response.status_code == 400
    assert "không thể tự chờ chính nó" in response.json()["detail"][0]["message"]


def test_order_rejects_id_not_from_system(client):
    data = extract(client, 1)
    plan = data["plan"]
    plan["tasks"][0]["id"] = "A"
    assert order(client, data["run_id"], plan).status_code == 400


def test_order_rejects_extra_field(client):
    data = extract(client, 1)
    plan = {**data["plan"], "order": ["T04", "T03", "T02", "T01"]}
    assert order(client, data["run_id"], plan).status_code == 422


def test_unknown_run_is_404(client):
    assert order(client, 999, {"tasks": [], "dependencies": []}).status_code == 404
    assert validate(client, 999, {"tasks": [], "dependencies": []}).status_code == 404
    assert client.get("/plans/orders/999").status_code == 404


def test_run_of_other_module_is_404(client):
    run_id = client.post("/expenses/extract", json={"text": load_samples("expenses")[0]["input"]}).json()["run_id"]
    assert order(client, run_id, {"tasks": [], "dependencies": []}).status_code == 404


def test_model_failure_run_can_still_be_filled_by_hand(client):
    # GenAI không đọc được (mock không có mẫu): người dùng vẫn tự nhập việc và tính được
    data = client.post("/plans/extract", json={"text": "Biên bản chưa có trong mẫu"}).json()
    assert data["plan"] is None
    plan = {"tasks": [{"id": None, "title": "Việc 1"}, {"id": None, "title": "Việc 2"}], "dependencies": []}
    filled = validate(client, data["run_id"], plan).json()["plan"]
    filled["dependencies"] = [{"before": "T02", "after": "T01"}]
    out = order(client, data["run_id"], filled).json()
    assert ids(out["steps"]) == ["T02", "T01"]
    run = client.get(f"/runs/{data['run_id']}").json()
    assert run["result"]["edited_fields"] == ["dependencies", "tasks"]


def test_orders_survive_restart(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/restart.db", mock_llm=True)
    with TestClient(create_app(settings)) as c:
        data = extract(c, 1)
        order_id = order(c, data["run_id"], data["plan"]).json()["order_id"]
    with TestClient(create_app(settings)) as c:
        assert ids(c.get(f"/plans/orders/{order_id}").json()["steps"]) == ["T01", "T02", "T03", "T04"]
