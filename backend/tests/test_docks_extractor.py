"""Test bước GenAI đọc email của Bài 02, dùng mock và monkeypatch. Không cần API key."""

from datetime import datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.llm import LLMResult, call_mock, load_samples
from app.core.timeutil import get_tz
from app.modules.docks import extractor
from app.modules.docks.extractor import SYSTEM_PROMPT, build_user_prompt, extract_dock_request
from app.modules.docks.schemas import ExtractRequest

SAMPLES = load_samples("docks")
MOCK = Settings(mock_llm=True)  # luôn chạy mock, không phụ thuộc file .env
VN = get_tz("Asia/Ho_Chi_Minh")


def run(sample: dict, request_id: str = "R007"):
    req = ExtractRequest(email=sample["input"], received_at=sample["received_at"])
    return extract_dock_request(req, request_id, MOCK)


def sample(label_start: str) -> dict:
    return next(s for s in SAMPLES if s["label"].startswith(label_start))


def test_samples_file_has_enough_cases():
    assert len(SAMPLES) >= 7


@pytest.mark.parametrize("item", SAMPLES, ids=[s["label"] for s in SAMPLES])
def test_every_sample_gives_expected_result(item):
    response, result = run(item)
    assert result.model == "mock"
    assert response.can_suggest is item["expect_can_suggest"]


# --- Ca thường --------------------------------------------------------------

def test_exercise_email_is_read_correctly():
    response, _ = run(sample("R01"), request_id="R001")
    e = response.extraction
    assert response.request_id == "R001"
    assert response.llm_output["request_id"] == "R001"  # mock đã thay "{request_id}"
    assert (e.carrier, e.duration_minutes, e.dock_type) == ("ACME Logistics", 60, "standard")
    assert e.earliest_start == datetime(2026, 10, 21, 9, 0, tzinfo=VN)  # gắn GMT+7
    assert response.issues == []


def test_refrigerated_email():
    response, _ = run(sample("Cần cửa lạnh"))
    assert response.extraction.dock_type == "refrigerated"


def test_utc_email_is_converted_to_warehouse_time():
    response, _ = run(sample("Email ghi giờ UTC"))
    start = response.extraction.earliest_start
    assert (start.hour, start.utcoffset()) == (9, timedelta(hours=7))


def test_original_llm_output_is_kept_unchanged():
    # llm_output giữ nguyên "02:00:00Z" để người dùng thấy GenAI đọc gì; extraction là bản đã đổi giờ
    response, _ = run(sample("Email ghi giờ UTC"))
    assert response.llm_output["earliest_start"] == "2026-10-21T02:00:00Z"


# --- Thiếu thông tin / sát giới hạn -----------------------------------------

def test_missing_duration_asks_user_instead_of_guessing():
    response, _ = run(sample("Thiếu thời lượng"))
    assert response.can_suggest is False
    assert response.extraction.duration_minutes is None  # không đoán từ 12 pallet
    assert [i.field for i in response.issues if i.level == "error"] == ["duration_minutes"]


def test_after_closing_is_only_warning():
    response, _ = run(sample("Đến sau giờ đóng cửa"))
    assert response.can_suggest is True
    assert "sau giờ đóng cửa" in response.issues[0].message


# --- Dữ liệu sai ------------------------------------------------------------

def test_negative_duration_is_blocked():
    response, _ = run(sample("[Lỗi] Giờ kết thúc"))
    assert response.extraction is None
    assert [i.field for i in response.issues] == ["duration_minutes"]


def test_request_id_copied_from_email_is_rejected():
    response, _ = run(sample("[Lỗi] GenAI tự đặt mã"), request_id="R008")
    assert response.can_suggest is False
    assert response.issues[0].field == "request_id"
    assert "R008" in response.issues[0].message


def test_model_call_failure_blocks_suggest(monkeypatch):
    monkeypatch.setattr(
        extractor, "extract_structured",
        lambda *args, **kwargs: LLMResult(raw=None, model="gpt-4o-mini", latency_ms=30_000, error="Gọi model thất bại: timeout"),
    )
    response, _ = extract_dock_request(ExtractRequest(email="Xe đến 9 giờ"), "R009", MOCK)
    assert response.can_suggest is False
    assert response.extraction is None
    assert "timeout" in response.issues[0].message


def test_mock_unknown_email_cannot_suggest():
    response, _ = extract_dock_request(ExtractRequest(email="Nội dung không có trong mẫu"), "R010", MOCK)
    assert response.can_suggest is False
    assert response.llm_output is None


# --- Prompt -----------------------------------------------------------------

def test_prompt_has_request_id_received_date_and_email():
    received = datetime(2026, 10, 20, 16, 0, tzinfo=VN)
    prompt = build_user_prompt("Sáng mai 8 giờ xe tới", "R004", received)
    assert "Mã yêu cầu hệ thống đã cấp: R004" in prompt
    assert "Thứ Ba, 2026-10-20 16:00 (GMT+7)" in prompt  # để GenAI hiểu "sáng mai"
    assert "<email>\nSáng mai 8 giờ xe tới\n</email>" in prompt


def test_missing_received_at_uses_now():
    now = datetime(2026, 10, 20, 9, 0, tzinfo=VN)
    response, _ = extract_dock_request(ExtractRequest(email=sample("R01")["input"]), "R011", MOCK, now=now)
    assert response.can_suggest is True


# --- Mock thay placeholder --------------------------------------------------

def test_mock_fills_placeholder_without_changing_sample_file(tmp_path):
    (tmp_path / "demo.json").write_text(
        '[{"input": "xe đến", "llm_output": {"request_id": "{request_id}", "tags": ["{request_id}", "x"]}}]',
        encoding="utf-8",
    )
    first = call_mock("demo", "xe đến", tmp_path, {"request_id": "R001"})
    second = call_mock("demo", "xe đến", tmp_path, {"request_id": "R002"})
    assert first.raw == {"request_id": "R001", "tags": ["R001", "x"]}
    assert second.raw["request_id"] == "R002"  # mẫu gốc không bị sửa sau lần đầu


@pytest.mark.parametrize("rule", [
    "KHÔNG tự đổi sang UTC",                    # giờ trong email là giờ kho, giữ nguyên
    "Ô nào đã có giá trị thì KHÔNG ghi vào",    # missing_fields chỉ ghi ô đang null
    "Email đủ thông tin thì để []",
    "KHÔNG tự đoán từ loại hàng",               # thời lượng
])
def test_system_prompt_states_rules(rule):
    assert rule in SYSTEM_PROMPT
