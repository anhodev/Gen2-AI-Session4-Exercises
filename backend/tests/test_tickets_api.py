"""Test API Bài 01 bằng TestClient. Mock GenAI + SQLite tạm, không cần mạng."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.llm import load_samples
from app.main import create_app

# Tin nhắn mẫu trong samples/tickets.json, đánh chỉ mục theo số thứ tự: 1 = "Mẫu 1 - ..."
SAMPLES = {i: {**s, "message": s["input"]} for i, s in enumerate(load_samples("tickets"), start=1)}


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


def extract(client, n: int) -> dict:
    s = SAMPLES[n]
    response = client.post("/tickets/extract", json={"received_at": s["received_at"], "message": s["message"]})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, n: int, **edits):
    """Extract mẫu thứ n rồi xác nhận, có thể sửa vài ô trước khi xác nhận."""
    data = extract(client, n)
    ticket = {**data["llm_output"], **edits}
    return client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": ticket})


def queue_ids(client, limit: int = 10) -> list[str]:
    return [item["ticket_id"] for item in client.get(f"/tickets/queue?limit={limit}").json()["items"]]


# --- Ca thường --------------------------------------------------------------

def test_health_reports_mock_and_sqlite(client):
    assert client.get("/health").json() == {"status": "ok", "llm": "mock", "database": "sqlite"}


def test_samples_endpoint_lists_messages(client):
    samples = client.get("/tickets/samples").json()
    assert samples[0]["label"].startswith("Mẫu 1 ")
    assert samples[0]["message"] == SAMPLES[1]["message"]
    assert "ticket_id" not in samples[0]  # mẫu không có mã, backend cấp lúc extract


def test_extract_returns_llm_output_and_run_id(client):
    data = extract(client, 1)
    assert data["run_id"] > 0
    assert data["can_confirm"] is True
    assert data["llm_output"]["severity"] == "high"


def test_extract_generates_ticket_id_and_saves_run(client):
    data = extract(client, 1)
    assert data["ticket_id"] == "T01"
    assert data["llm_output"]["ticket_id"] == "T01"
    run = client.get(f"/runs/{data['run_id']}").json()
    assert run["raw_input"]["ticket_id"] == "T01"


def test_ticket_ids_are_sequential(client):
    # Gửi lần lượt mẫu 1, 2, 3 -> T01, T02, T03 như ví dụ trong đề
    assert [extract(client, n)["ticket_id"] for n in [1, 2, 3]] == ["T01", "T02", "T03"]


def test_ticket_id_from_browser_is_ignored(client):
    s = SAMPLES[1]
    data = client.post("/tickets/extract", json={
        "ticket_id": "HACK", "received_at": s["received_at"], "message": s["message"],
    }).json()
    assert data["ticket_id"] == "T01"


def test_extract_does_not_add_to_queue(client):
    extract(client, 1)
    assert client.get("/tickets/queue").json()["total"] == 0


def test_sample_order_and_top_k(client):
    for n in [1, 2, 3]:
        assert confirm(client, n).status_code == 200
    assert queue_ids(client) == ["T02", "T01", "T03"]
    assert queue_ids(client, limit=2) == ["T02", "T01"]


def test_viewing_top_k_does_not_change_next(client):
    for n in [1, 2, 3]:
        confirm(client, n)
    queue_ids(client, limit=2)
    queue_ids(client, limit=2)
    assert client.post("/tickets/next").json()["ticket_id"] == "T02"
    assert queue_ids(client) == ["T01", "T03"]


def test_t04_jumps_to_front(client):
    for n in [1, 2, 3, 4]:
        confirm(client, n)
    assert queue_ids(client) == ["T04", "T02", "T01", "T03"]


def test_confirm_returns_deadline_in_display_timezone(client):
    body = confirm(client, 2).json()
    assert body["deadline_at"] == "2026-10-21T09:50:00+07:00"
    assert body["sla_minutes"] == 30


def test_queue_item_has_reason_and_details(client):
    confirm(client, 1)
    confirm(client, 2)
    first = client.get("/tickets/queue?limit=1").json()["items"][0]
    assert first["intent"] == "technical" and first["product"] == "checkout"
    assert "hạn sớm hơn T01" in first["reason"]


def test_user_edit_is_saved_in_history(client):
    body = confirm(client, 3, severity="high").json()  # người dùng sửa medium -> high
    assert body["edited_fields"] == ["severity"]

    run = client.get("/runs?module=tickets&limit=1").json()[0]
    assert run["status"] == "confirmed"
    assert run["llm_output"]["severity"] == "medium"
    assert run["user_output"]["severity"] == "high"
    assert run["raw_input"]["message"] == SAMPLES[3]["message"]


def test_queue_survives_restart(db_url):
    with make_client(db_url) as c:
        for n in [1, 2, 3]:
            confirm(c, n)
        c.post("/tickets/next")  # lấy T02 ra
    with make_client(db_url) as c:  # khởi động lại app
        assert queue_ids(c) == ["T01", "T03"]
        assert extract(c, 1)["ticket_id"] == "T04"  # mã cấp tiếp, không quay lại T01


# --- Ca sát giới hạn --------------------------------------------------------

def test_warning_does_not_block_confirm(client):
    data = extract(client, 5)  # tin nhắn mơ hồ
    assert data["can_confirm"] is True
    assert data["issues"][0]["level"] == "warning"
    assert confirm(client, 5).json()["warnings"][0]["field"] == "needs_human_review"


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
        "received_at": "2026-10-21T09:00:00", "message": "Tôi bị trừ tiền hai lần",
    })
    assert response.status_code == 422


def test_bad_severity_from_genai_cannot_confirm(client):
    data = extract(client, 6)  # GenAI trả "urgent"
    assert data["can_confirm"] is False
    response = client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": data["llm_output"]})
    assert response.status_code == 422
    assert client.get("/tickets/queue").json()["total"] == 0


def test_bad_severity_fixed_by_user_can_confirm(client):
    assert confirm(client, 6, severity="high").status_code == 200


def test_wrong_ticket_id_is_400(client):
    data = extract(client, 7)  # backend cấp T01 nhưng GenAI tự đặt T70
    assert data["can_confirm"] is False
    response = client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": data["llm_output"]})
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "ticket_id"
    assert client.get("/tickets/queue").json()["total"] == 0


def test_wrong_ticket_id_confirmed_with_system_id(client):
    # UI không cho sửa mã: luôn gửi mã hệ thống đã cấp, nên vẫn xác nhận được
    data = extract(client, 7)
    ticket = {**data["llm_output"], "ticket_id": data["ticket_id"]}
    body = client.post("/tickets/confirm", json={"run_id": data["run_id"], "ticket": ticket}).json()
    assert body["ticket_id"] == "T01"
    assert body["edited_fields"] == []  # hệ thống thay mã, không tính là người dùng sửa
    assert queue_ids(client) == ["T01"]


def test_tampered_ticket_id_is_400(client):
    # Ai đó gửi thẳng API với mã khác mã đã cấp -> từ chối
    response = confirm(client, 1, ticket_id="T99")
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "ticket_id"


def test_same_message_twice_gets_two_tickets(client):
    # Mỗi lần extract là một mã mới, nên gửi lại cùng tin nhắn không còn bị trùng mã
    assert [confirm(client, 1).json()["ticket_id"] for _ in range(2)] == ["T01", "T02"]
    assert queue_ids(client) == ["T01", "T02"]


def test_same_run_cannot_be_confirmed_twice(client):
    data = extract(client, 1)
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
        "received_at": "2026-10-21T09:00:00+07:00", "message": "Nội dung không có trong mẫu",
    })
    data = response.json()
    assert data["can_confirm"] is False
    assert data["llm_output"] is None
