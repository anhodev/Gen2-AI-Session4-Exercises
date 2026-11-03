"""Test bước GenAI đọc tin nhắn của Bài 01, dùng mock và monkeypatch."""

import pytest

from app.core.config import Settings
from app.core.llm import LLMResult, fill_placeholders, load_samples
from app.modules.tickets import extractor
from app.modules.tickets.extractor import build_user_prompt, extract_ticket
from app.modules.tickets.schemas import ExtractRequest

SAMPLES = load_samples("tickets")
MOCK = Settings(mock_llm=True)  # luôn chạy mock, không phụ thuộc file .env


def request_from(sample: dict) -> ExtractRequest:
    return ExtractRequest(received_at=sample["received_at"], message=sample["input"])


def sample(n: int) -> dict:
    return next(s for s in SAMPLES if s["label"].startswith(f"Mẫu {n} "))


def test_samples_file_is_not_empty():
    assert len(SAMPLES) >= 5


@pytest.mark.parametrize("s", SAMPLES, ids=[s["label"][:6] for s in SAMPLES])
def test_every_sample_gives_expected_result(s):
    response, result = extract_ticket(request_from(s), "T05", MOCK)
    assert result.model == "mock"
    # JSON gốc giữ nguyên, chỉ "{ticket_id}" được thay bằng mã backend cấp
    assert response.llm_output == fill_placeholders(s["llm_output"], {"ticket_id": "T05"})
    expect_error = "[Lỗi]" in s["label"]
    assert response.can_confirm is not expect_error


def test_response_uses_ticket_id_from_backend():
    response, _ = extract_ticket(request_from(sample(1)), "T42", MOCK)
    assert response.ticket_id == "T42"
    assert response.llm_output["ticket_id"] == "T42"  # mock đã thay "{ticket_id}"


def test_bad_severity_sample_points_to_severity_field():
    response, _ = extract_ticket(request_from(sample(6)), "T06", MOCK)
    assert [i.field for i in response.issues] == ["severity"]


def test_wrong_id_sample_points_to_ticket_id_field():
    response, _ = extract_ticket(request_from(sample(7)), "T07", MOCK)  # GenAI tự đặt T70
    assert [i.field for i in response.issues] == ["ticket_id"]


def test_model_call_failure_blocks_confirm(monkeypatch):
    # Giả lập gọi model bị timeout
    monkeypatch.setattr(
        extractor, "extract_structured",
        lambda *args, **kwargs: LLMResult(raw=None, model="gpt-4o-mini", latency_ms=30_000, error="Gọi model thất bại: timeout"),
    )
    req = ExtractRequest(received_at="2026-10-21T09:00:00+07:00", message="Không vào được app")
    response, _ = extract_ticket(req, "T09")
    assert response.can_confirm is False
    assert response.llm_output is None
    assert "timeout" in response.issues[0].message


def test_prompt_contains_system_ticket_id_and_message():
    req = ExtractRequest(received_at="2026-10-21T09:00:00+07:00", message="Không vào được app")
    prompt = build_user_prompt(req, "T09")
    assert "T09" in prompt
    assert "<message>\nKhông vào được app\n</message>" in prompt
