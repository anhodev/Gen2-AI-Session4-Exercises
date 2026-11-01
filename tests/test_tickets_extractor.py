"""Test bước GenAI đọc tin nhắn của Bài 01, dùng mock và monkeypatch."""

import pytest

from app.core.config import Settings
from app.core.llm import LLMResult, load_samples
from app.modules.tickets import extractor
from app.modules.tickets.extractor import build_user_prompt, extract_ticket
from app.modules.tickets.schemas import ExtractRequest

SAMPLES = load_samples("tickets")
MOCK = Settings(mock_llm=True)  # luôn chạy mock, không phụ thuộc file .env


def request_from(sample: dict) -> ExtractRequest:
    return ExtractRequest(ticket_id=sample["ticket_id"], received_at=sample["received_at"], message=sample["input"])


def test_samples_file_is_not_empty():
    assert len(SAMPLES) >= 5


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["ticket_id"] for s in SAMPLES])
def test_every_sample_gives_expected_result(sample):
    response, result = extract_ticket(request_from(sample), MOCK)
    assert result.model == "mock"
    assert response.llm_output == sample["llm_output"]  # JSON gốc giữ nguyên
    expect_error = "[Lỗi]" in sample["label"]
    assert response.can_confirm is not expect_error


def test_bad_severity_sample_points_to_severity_field():
    sample = next(s for s in SAMPLES if s["ticket_id"] == "T06")
    response, _ = extract_ticket(request_from(sample), MOCK)
    assert [i.field for i in response.issues] == ["severity"]


def test_wrong_id_sample_points_to_ticket_id_field():
    sample = next(s for s in SAMPLES if s["ticket_id"] == "T07")
    response, _ = extract_ticket(request_from(sample), MOCK)
    assert [i.field for i in response.issues] == ["ticket_id"]


def test_model_call_failure_blocks_confirm(monkeypatch):
    # Giả lập gọi model bị timeout
    monkeypatch.setattr(
        extractor, "extract_structured",
        lambda *args: LLMResult(raw=None, model="gpt-4o-mini", latency_ms=30_000, error="Gọi model thất bại: timeout"),
    )
    req = ExtractRequest(ticket_id="T09", received_at="2026-10-21T09:00:00+07:00", message="Không vào được app")
    response, _ = extract_ticket(req)
    assert response.can_confirm is False
    assert response.llm_output is None
    assert "timeout" in response.issues[0].message


def test_prompt_contains_system_ticket_id_and_message():
    req = ExtractRequest(ticket_id="T09", received_at="2026-10-21T09:00:00+07:00", message="Không vào được app")
    prompt = build_user_prompt(req)
    assert "T09" in prompt
    assert "<message>\nKhông vào được app\n</message>" in prompt
