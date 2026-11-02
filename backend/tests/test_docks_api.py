"""Test API Bài 02 bằng TestClient. Mock GenAI + SQLite tạm, không cần mạng."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.llm import load_samples
from app.main import create_app

SAMPLES = load_samples("docks")


def sample(label_start: str) -> dict:
    return next(s for s in SAMPLES if s["label"].startswith(label_start))


def make_client(db_url: str) -> TestClient:
    return TestClient(create_app(Settings(database_url=db_url, mock_llm=True)))


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/api.db"


@pytest.fixture
def client(db_url):
    # "with" để chạy lifespan: tạo bảng + seed cửa kho
    with make_client(db_url) as c:
        yield c


def extract(client, label: str) -> dict:
    s = sample(label)
    response = client.post("/docks/requests/extract", json={"email": s["input"], "received_at": s["received_at"]})
    assert response.status_code == 200, response.text
    return response.json()


def suggest(client, data: dict, **edits):
    return client.post("/docks/slots/suggest", json={"run_id": data["run_id"], "request": {**data["extraction"], **edits}})


def confirm(client, data: dict, dock_id: str, start: str, **edits):
    return client.post("/docks/bookings/confirm", json={
        "run_id": data["run_id"], "request": {**data["extraction"], **edits}, "dock_id": dock_id, "start": start,
    })


def dock_bookings(client, dock_id: str, day: str = "2026-10-21") -> list[dict]:
    docks = client.get(f"/docks/schedule?date={day}").json()["docks"]
    return next(d for d in docks if d["dock_id"] == dock_id)["bookings"]


# --- Ca thường --------------------------------------------------------------

def test_samples_endpoint_lists_emails(client):
    samples = client.get("/docks/samples").json()
    assert len(samples) == len(SAMPLES)
    assert samples[0]["email"] == SAMPLES[0]["input"]


def test_extract_generates_request_id_and_saves_run(client):
    data = extract(client, "R01")
    assert data["request_id"] == "R001"
    assert data["extraction"]["earliest_start"] == "2026-10-21T09:00:00+07:00"
    assert data["can_suggest"] is True
    run = client.get(f"/runs/{data['run_id']}").json()
    assert (run["module"], run["status"], run["raw_input"]["request_id"]) == ("docks", "extracted", "R001")


def test_request_ids_are_sequential(client):
    assert [extract(client, "R01")["request_id"] for _ in range(3)] == ["R001", "R002", "R003"]


def test_suggest_exercise_example_gives_b_at_10_15(client):
    slot = suggest(client, extract(client, "R01")).json()
    assert (slot["status"], slot["dock_id"]) == ("ok", "B")
    assert (slot["start"], slot["end"]) == ("2026-10-21T10:15:00+07:00", "2026-10-21T11:15:00+07:00")
    assert "GMT+7" in slot["reason"]
    free = {c["dock_id"]: c["free_from"] for c in slot["checks"]}
    assert free == {"A": "2026-10-21T11:00:00+07:00", "B": "2026-10-21T10:15:00+07:00"}


def test_suggest_does_not_book(client):
    data = extract(client, "R01")
    suggest(client, data)
    assert len(dock_bookings(client, "B")) == 1  # chỉ có lịch seed
    assert client.get(f"/runs/{data['run_id']}").json()["status"] == "extracted"


def test_confirm_books_and_changes_schedule(client):
    data = extract(client, "R01")
    response = confirm(client, data, "B", "2026-10-21T10:15:00+07:00")
    assert response.status_code == 200, response.text
    booking = response.json()
    assert (booking["request_id"], booking["dock_id"], booking["carrier"]) == ("R001", "B", "ACME Logistics")
    assert booking["end"] == "2026-10-21T11:15:00+07:00"
    assert booking["edited_fields"] == []

    assert [b["request_id"] for b in dock_bookings(client, "B")] == [None, "R001"]
    run = client.get(f"/runs/{data['run_id']}").json()
    assert (run["status"], run["result"]["dock_id"]) == ("confirmed", "B")
    # Xe sau cùng giờ: B đã kín tới 11:15 -> A 11:00
    assert suggest(client, extract(client, "R01")).json()["dock_id"] == "A"


def test_refrigerated_goes_to_dock_c(client):
    slot = suggest(client, extract(client, "Cần cửa lạnh")).json()
    assert (slot["dock_id"], slot["start"]) == ("C", "2026-10-21T11:00:00+07:00")


def test_schedule_shows_timezone_and_hours(client):
    schedule = client.get("/docks/schedule?date=2026-10-21").json()
    assert schedule["timezone"] == "GMT+7"
    assert (schedule["open_at"], schedule["close_at"]) == ("2026-10-21T08:00:00+07:00", "2026-10-21T18:00:00+07:00")
    assert [(d["dock_id"], len(d["bookings"])) for d in schedule["docks"]] == [("A", 2), ("B", 1), ("C", 1)]


# --- Người dùng sửa / so sánh ô đã sửa ---------------------------------------

def test_missing_duration_blocks_suggest_until_user_fills_it(client):
    data = extract(client, "Thiếu thời lượng")
    blocked = suggest(client, data)
    assert blocked.status_code == 400
    assert "duration_minutes" in [i["field"] for i in blocked.json()["detail"]]

    edits = {"duration_minutes": 60, "missing_fields": []}
    slot = suggest(client, data, **edits).json()
    assert slot["status"] == "ok"
    booking = confirm(client, data, slot["dock_id"], slot["start"], **edits).json()
    assert booking["edited_fields"] == ["duration_minutes", "missing_fields"]


def test_utc_email_is_not_counted_as_edited(client):
    # GenAI trả "02:00Z", bản đã chuẩn hóa là "09:00+07:00": cùng thời điểm, không phải người dùng sửa
    data = extract(client, "Email ghi giờ UTC")
    slot = suggest(client, data).json()
    booking = confirm(client, data, slot["dock_id"], slot["start"]).json()
    assert booking["edited_fields"] == []


# --- Ca sát giới hạn --------------------------------------------------------

def test_after_closing_has_no_slot(client):
    slot = suggest(client, extract(client, "Đến sau giờ đóng cửa")).json()
    assert (slot["status"], slot["dock_id"]) == ("no_slot", None)
    assert "đóng cửa" in slot["reason"]


def test_confirm_outside_working_hours_is_rejected(client):
    response = confirm(client, extract(client, "R01"), "A", "2026-10-21T17:30:00+07:00")
    assert response.status_code == 400
    assert "ngoài giờ mở cửa" in response.json()["detail"]


def test_confirm_before_truck_arrives_is_rejected(client):
    response = confirm(client, extract(client, "R01"), "A", "2026-10-21T08:00:00+07:00")
    assert response.status_code == 400
    assert "sớm hơn giờ xe đến" in response.json()["detail"]


def test_confirm_in_utc_is_same_moment(client):
    # 03:15Z = 10:15 GMT+7: trình duyệt gửi múi giờ nào cũng được
    booking = confirm(client, extract(client, "R01"), "B", "2026-10-21T03:15:00Z").json()
    assert booking["start"] == "2026-10-21T10:15:00+07:00"


# --- Hai người cùng đặt -----------------------------------------------------

def test_second_person_gets_409_with_new_suggestion(client):
    first, second = extract(client, "R01"), extract(client, "R01")
    # Cả hai cùng được gợi ý B 10:15
    assert suggest(client, first).json()["start"] == suggest(client, second).json()["start"]
    assert confirm(client, first, "B", "2026-10-21T10:15:00+07:00").status_code == 200

    late = confirm(client, second, "B", "2026-10-21T10:15:00+07:00")
    assert late.status_code == 409
    detail = late.json()["detail"]
    assert "trùng" in detail["message"]
    assert (detail["suggestion"]["dock_id"], detail["suggestion"]["start"]) == ("A", "2026-10-21T11:00:00+07:00")

    # Run chưa bị đánh dấu rejected: người thứ hai đặt lại được với gợi ý mới
    assert client.get(f"/runs/{second['run_id']}").json()["status"] == "extracted"
    assert confirm(client, second, "A", detail["suggestion"]["start"]).status_code == 200


def test_same_request_cannot_be_confirmed_twice(client):
    data = extract(client, "R01")
    assert confirm(client, data, "B", "2026-10-21T10:15:00+07:00").status_code == 200
    again = confirm(client, data, "A", "2026-10-21T11:00:00+07:00")
    assert again.status_code == 409
    assert "đã được xử lý" in again.json()["detail"]


# --- Dữ liệu sai ------------------------------------------------------------

def test_tampered_request_id_is_rejected(client):
    response = suggest(client, extract(client, "R01"), request_id="R999")
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "request_id"


def test_negative_duration_fails_schema(client):
    assert suggest(client, extract(client, "R01"), duration_minutes=-60).status_code == 422


def test_wrong_dock_type_is_rejected(client):
    response = confirm(client, extract(client, "Cần cửa lạnh"), "A", "2026-10-21T11:00:00+07:00")
    assert response.status_code == 400
    assert "refrigerated" in response.json()["detail"]


def test_unknown_dock_is_rejected(client):
    assert confirm(client, extract(client, "R01"), "Z", "2026-10-21T10:15:00+07:00").status_code == 400


def test_unknown_run_is_404(client):
    data = extract(client, "R01")
    assert suggest(client, {**data, "run_id": 9999}).status_code == 404


def test_ticket_run_cannot_be_used_for_docks(client):
    ticket = load_samples("tickets")[0]
    run_id = client.post("/tickets/extract", json={
        "ticket_id": ticket["ticket_id"], "received_at": ticket["received_at"], "message": ticket["input"],
    }).json()["run_id"]
    data = extract(client, "R01")
    assert suggest(client, {**data, "run_id": run_id}).status_code == 404


def test_seed_is_not_duplicated_after_restart(db_url):
    for _ in range(2):
        with make_client(db_url) as c:
            assert len(dock_bookings(c, "A")) == 2


def test_two_people_confirming_at_the_same_time(client):
    # Chạy song song thật (2 thread): khóa trong app bảo đảm chỉ 1 người đặt được
    from concurrent.futures import ThreadPoolExecutor

    runs = [extract(client, "R01") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(lambda d: confirm(client, d, "B", "2026-10-21T10:15:00+07:00").status_code, runs))
    assert codes == [200, 409]
    assert [b["request_id"] for b in dock_bookings(client, "B")].count(None) == 1
    assert len(dock_bookings(client, "B")) == 2
