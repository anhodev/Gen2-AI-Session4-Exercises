"""Test schema + kiểm tra kết quả GenAI của Bài 01.

Giả lập "GenAI trả về" bằng dict viết tay -> không cần API key.
"""

import pytest
from pydantic import ValidationError

from app.modules.tickets.schemas import ConfirmRequest, ExtractRequest, TicketExtraction
from app.modules.tickets.validation import has_errors, validate_extraction


def good_output(**overrides) -> dict:
    """JSON mẫu trong đề, có thể đè từng ô để tạo ca sai."""
    data = {
        "ticket_id": "T01",
        "intent": "billing",
        "severity": "high",
        "product": "checkout",
        "summary": "Khách bị tính tiền hai lần",
        "needs_human_review": False,
    }
    data.update(overrides)
    return data


def fields_with_error(issues) -> set[str]:
    return {i.field for i in issues if i.level == "error"}


# --- Mẫu JSON gửi cho GenAI -------------------------------------------------

def test_json_schema_lists_allowed_values():
    schema = TicketExtraction.model_json_schema()
    props = schema["properties"]
    assert props["severity"]["enum"] == ["low", "medium", "high", "critical"]
    assert props["intent"]["enum"] == ["billing", "technical", "account", "other"]
    assert {"type": "null"} in props["product"]["anyOf"]  # product được phép null
    assert props["summary"]["maxLength"] == 200
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(props)  # mọi ô đều phải có mặt (null cũng phải ghi rõ)


# --- Ca thường --------------------------------------------------------------

def test_sample_output_is_valid():
    parsed, issues = validate_extraction(good_output(), "T01")
    assert parsed is not None and parsed.severity == "high"
    assert issues == []


def test_product_null_is_allowed():
    parsed, issues = validate_extraction(good_output(product=None), "T01")
    assert parsed.product is None
    assert not has_errors(issues)


def test_ambiguous_message_gives_warning_not_error():
    _, issues = validate_extraction(good_output(intent="other", needs_human_review=True), "T01")
    assert not has_errors(issues)
    assert [i.field for i in issues] == ["needs_human_review"]


# --- Ca sát giới hạn --------------------------------------------------------

def test_summary_exactly_200_chars_is_ok():
    _, issues = validate_extraction(good_output(summary="a" * 200), "T01")
    assert not has_errors(issues)


def test_summary_201_chars_is_error():
    parsed, issues = validate_extraction(good_output(summary="a" * 201), "T01")
    assert parsed is None
    assert fields_with_error(issues) == {"summary"}
    assert "200" in issues[0].message


def test_intent_other_without_review_is_warning():
    _, issues = validate_extraction(good_output(intent="other"), "T01")
    assert not has_errors(issues)
    assert issues[0].field == "intent"


# --- Dữ liệu sai: JSON đúng khuôn nhưng nội dung sai ------------------------

@pytest.mark.parametrize("bad", ["urgent", "HIGH", "P1", None])
def test_severity_outside_list_is_error(bad):
    parsed, issues = validate_extraction(good_output(severity=bad), "T01")
    assert parsed is None
    assert fields_with_error(issues) == {"severity"}


def test_wrong_ticket_id_is_error():
    parsed, issues = validate_extraction(good_output(ticket_id="T99"), "T01")
    assert parsed is not None  # đúng khuôn...
    assert fields_with_error(issues) == {"ticket_id"}  # ...nhưng sai nội dung
    assert "T01" in issues[0].message


def test_blank_summary_is_error():
    _, issues = validate_extraction(good_output(summary="   "), "T01")
    assert fields_with_error(issues) == {"summary"}


def test_missing_and_extra_fields_are_reported_together():
    raw = good_output(deadline="2026-10-21T11:00:00+07:00")  # GenAI tự đoán hạn -> không được
    del raw["intent"]
    _, issues = validate_extraction(raw, "T01")
    assert fields_with_error(issues) == {"intent", "deadline"}


def test_non_object_output_is_error():
    _, issues = validate_extraction("không phải JSON", "T01")
    assert has_errors(issues)


# --- Request của API --------------------------------------------------------

def test_received_at_without_timezone_is_rejected():
    with pytest.raises(ValidationError) as exc:
        ExtractRequest(received_at="2026-10-21T09:00:00", message="Tôi bị trừ tiền 2 lần")
    assert exc.value.errors()[0]["type"] == "timezone_aware"


def test_confirm_request_rejects_bad_severity_from_user_edit():
    with pytest.raises(ValidationError):
        ConfirmRequest(run_id=1, ticket=good_output(severity="urgent"))
