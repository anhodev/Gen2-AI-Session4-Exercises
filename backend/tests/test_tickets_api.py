"""Test API Bài 01 bằng TestClient. Mock GenAI + SQLite tạm, không cần mạng."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.llm import load_samples
from app.main import create_app

# Tin nhắn mẫu trong samples/tickets.json, đánh chỉ mục theo ticket_id
SAMPLES = {s["ticket_id"]: {**s, "message": s["input"]} for s in load_samples("tickets")}


def make_client(db_url: str) -> TestClient:
    app = create_app(Settings(database_url=db_url, mock_llm=True))
    return TestClient(app)


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/api.db"


@pytest.fixture
def client(db_url):
    # "with" để chạy lifespan: tạo bảng + dựng lại heap
    with make_client(db_url) as c:
        yield c


def extract(client, ticket_id: str) -> dict:
    s = SAMPLES[ticket_id]
    response = client.post("/tickets/extract", json={
        "ticket_id": s["ticket_id"], "received_at": s["received_at"], "message": s["message"],
    })
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, ticket_id: str, **edits):
    """Extract một mẫu rồi xác nhận, có thể sửa vài ô trước khi xác nhận."""
    data = extract(client, ticket_id)
    ticket = {**data["llm_output"], **edits}
    return client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": ticket})


def queue_ids(client, limit: int = 10) -> list[str]:
    return [item["ticket_id"] for item in client.get(f"/tickets/queue?limit={limit}").json()["items"]]


# --- Ca thường --------------------------------------------------------------

def test_health_reports_mock_and_sqlite(client):
    assert client.get("/health").json() == {"status": "ok", "llm": "mock", "database": "sqlite"}


def test_samples_endpoint_lists_messages(client):
    samples = client.get("/tickets/samples").json()
    assert [s["ticket_id"] for s in samples][:3] == ["T01", "T02", "T03"]
    assert samples[0]["message"] == SAMPLES["T01"]["message"]


def test_extract_returns_llm_output_and_run_id(client):
    data = extract(client, "T01")
    assert data["run_id"] > 0
    assert data["can_confirm"] is True
    assert data["llm_output"]["severity"] == "high"


def test_extract_does_not_add_to_queue(client):
    extract(client, "T01")
    assert client.get("/tickets/queue").json()["total"] == 0


def test_sample_order_and_top_k(client):
    for tid in ["T01", "T02", "T03"]:
        assert confirm(client, tid).status_code == 200
    assert queue_ids(client) == ["T02", "T01", "T03"]
    assert queue_ids(client, limit=2) == ["T02", "T01"]


def test_viewing_top_k_does_not_change_next(client):
    for tid in ["T01", "T02", "T03"]:
        confirm(client, tid)
    queue_ids(client, limit=2)
    queue_ids(client, limit=2)
    assert client.post("/tickets/next").json()["ticket_id"] == "T02"
    assert queue_ids(client) == ["T01", "T03"]


def test_t04_jumps_to_front(client):
    for tid in ["T01", "T02", "T03", "T04"]:
        confirm(client, tid)
    assert queue_ids(client) == ["T04", "T02", "T01", "T03"]


def test_confirm_returns_deadline_in_display_timezone(client):
    body = confirm(client, "T02").json()
    assert body["deadline_at"] == "2026-10-21T09:50:00+07:00"
    assert body["sla_minutes"] == 30


def test_queue_item_has_reason_and_details(client):
    confirm(client, "T01")
    confirm(client, "T02")
    first = client.get("/tickets/queue?limit=1").json()["items"][0]
    assert first["intent"] == "technical" and first["product"] == "checkout"
    assert "hạn sớm hơn T01" in first["reason"]


def test_user_edit_is_saved_in_history(client):
    body = confirm(client, "T03", severity="high").json()  # người dùng sửa medium -> high
    assert body["edited_fields"] == ["severity"]

    run = client.get("/runs?module=tickets&limit=1").json()[0]
    assert run["status"] == "confirmed"
    assert run["llm_output"]["severity"] == "medium"
    assert run["user_output"]["severity"] == "high"
    assert run["raw_input"]["message"] == SAMPLES["T03"]["message"]


def test_queue_survives_restart(db_url):
    with make_client(db_url) as c:
        for tid in ["T01", "T02", "T03"]:
            confirm(c, tid)
        c.post("/tickets/next")  # lấy T02 ra
    with make_client(db_url) as c:  # khởi động lại app
        assert queue_ids(c) == ["T01", "T03"]


# --- Ca sát giới hạn --------------------------------------------------------

def test_warning_does_not_block_confirm(client):
    data = extract(client, "T05")  # tin nhắn mơ hồ
    assert data["can_confirm"] is True
    assert data["issues"][0]["level"] == "warning"
    assert confirm(client, "T05").json()["warnings"][0]["field"] == "needs_human_review"


def test_next_on_empty_queue(client):
    response = client.post("/tickets/next")
    assert response.status_code == 404
    assert response.json()["detail"] == "Hàng đợi trống"


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_queue_limit_out_of_range(client, limit):
    assert client.get(f"/tickets/queue?limit={limit}").status_code == 422


# --- Dữ liệu sai ------------------------------------------------------------

def test_received_at_without_timezone_is_422(client):
    response = client.post("/tickets/extract", json={
        "ticket_id": "T01", "received_at": "2026-10-21T09:00:00", "message": "Tôi bị trừ tiền hai lần",
    })
    assert response.status_code == 422


def test_bad_severity_from_genai_cannot_confirm(client):
    data = extract(client, "T06")  # GenAI trả "urgent"
    assert data["can_confirm"] is False
    response = client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": data["llm_output"]})
    assert response.status_code == 422
    assert client.get("/tickets/queue").json()["total"] == 0


def test_bad_severity_fixed_by_user_can_confirm(client):
    assert confirm(client, "T06", severity="high").status_code == 200


def test_wrong_ticket_id_is_400(client):
    data = extract(client, "T07")  # GenAI trả T70 thay vì T07
    assert data["can_confirm"] is False
    response = client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": data["llm_output"]})
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "ticket_id"


def test_duplicate_ticket_is_409_and_logged(client):
    confirm(client, "T01")
    response = confirm(client, "T01", severity="low")
    assert response.status_code == 409
    assert queue_ids(client) == ["T01"]
    assert client.get("/runs?module=tickets&limit=1").json()[0]["status"] == "rejected"


def test_same_run_cannot_be_confirmed_twice(client):
    data = extract(client, "T01")
    body = {"run_id": data["run_id"], "ticket": data["llm_output"]}
    assert client.post("/tickets/confirm", json=body).status_code == 200
    assert client.post("/tickets/confirm", json=body).status_code == 409


def test_unknown_run_is_404(client):
    response = client.post("/tickets/confirm", json={"run_id": 999, "ticket": {
        "ticket_id": "T01", "intent": "billing", "severity": "high",
        "product": None, "summary": "x", "needs_human_review": False,
    }})
    assert response.status_code == 404


def test_mock_unknown_message_cannot_confirm(client):
    response = client.post("/tickets/extract", json={
        "ticket_id": "T99", "received_at": "2026-10-21T09:00:00+07:00", "message": "Nội dung không có trong mẫu",
    })
    data = response.json()
    assert data["can_confirm"] is False
    assert data["llm_output"] is None
