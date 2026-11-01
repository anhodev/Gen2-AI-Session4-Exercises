"""Test schema + kiểm tra kết quả GenAI của Bài 02. Không cần API key."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.timeutil import get_tz
from app.modules.docks.algo import DockError
from app.modules.docks.schemas import ConfirmRequest, DockRequestExtraction, ExtractRequest
from app.modules.docks.validation import has_errors, to_slot_request, validate_extraction

VN = get_tz("Asia/Ho_Chi_Minh")


def good_output(**overrides) -> dict:
    """JSON mẫu trong đề, có thể đè từng ô để tạo ca sai."""
    data = {
        "request_id": "R01",
        "carrier": "ACME Logistics",
        "earliest_start": "2026-10-21T09:00:00+07:00",
        "duration_minutes": 60,
        "dock_type": "standard",
        "missing_fields": [],
    }
    data.update(overrides)
    return data


def check(raw, request_id="R01"):
    return validate_extraction(raw, request_id, VN)


def errors(issues) -> set[str]:
    return {i.field for i in issues if i.level == "error"}


def warnings(issues) -> list[str]:
    return [i.message for i in issues if i.level == "warning"]


# --- Mẫu JSON gửi cho GenAI -------------------------------------------------

def test_json_schema_lists_allowed_values():
    schema = DockRequestExtraction.model_json_schema()
    props = schema["properties"]
    assert props["dock_type"]["enum"] == ["standard", "refrigerated"]
    assert {"type": "null"} in props["duration_minutes"]["anyOf"]  # được phép null
    assert {"type": "integer", "exclusiveMinimum": 0} in props["duration_minutes"]["anyOf"]
    assert props["missing_fields"]["items"]["enum"] == ["carrier", "earliest_start", "duration_minutes"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(props)


# --- Ca thường --------------------------------------------------------------

def test_sample_output_is_valid():
    parsed, issues = check(good_output())
    assert issues == []
    assert parsed.earliest_start == datetime(2026, 10, 21, 9, 0, tzinfo=VN)


def test_time_without_timezone_gets_warehouse_timezone():
    parsed, issues = check(good_output(earliest_start="2026-10-21T09:00:00"))
    assert not has_errors(issues)
    assert parsed.earliest_start.utcoffset() == timedelta(hours=7)
    assert parsed.earliest_start.hour == 9


def test_other_timezone_is_converted_to_warehouse_time():
    parsed, _ = check(good_output(earliest_start="2026-10-21T02:00:00Z"))
    assert parsed.earliest_start.hour == 9
    assert parsed.earliest_start.utcoffset() == timedelta(hours=7)


def test_refrigerated_is_allowed():
    parsed, issues = check(good_output(dock_type="refrigerated"))
    assert parsed.dock_type == "refrigerated" and issues == []


def test_to_slot_request_feeds_the_algorithm():
    parsed, _ = check(good_output())
    request = to_slot_request(parsed)
    assert (request.duration_minutes, request.dock_type) == (60, "standard")


# --- Thiếu thông tin: phải hỏi lại, không đoán ------------------------------

def test_missing_duration_blocks_search():
    parsed, issues = check(good_output(duration_minutes=None, missing_fields=["duration_minutes"]))
    assert parsed is not None
    assert errors(issues) == {"duration_minutes"}
    assert "hỏi lại" in issues[0].message
    with pytest.raises(DockError):
        to_slot_request(parsed)


def test_null_duration_not_listed_in_missing_fields_is_flagged():
    _, issues = check(good_output(duration_minutes=None))
    assert errors(issues) == {"duration_minutes"}
    assert any("chưa ghi trong missing_fields" in w for w in warnings(issues))


def test_missing_fields_listing_a_filled_field_is_flagged():
    _, issues = check(good_output(missing_fields=["duration_minutes"]))
    assert not has_errors(issues)
    assert any("đã có giá trị" in w for w in warnings(issues))


def test_missing_earliest_start_blocks_search():
    _, issues = check(good_output(earliest_start=None, missing_fields=["earliest_start"]))
    assert errors(issues) == {"earliest_start"}


def test_missing_carrier_is_only_a_warning():
    _, issues = check(good_output(carrier=None, missing_fields=["carrier"]))
    assert not has_errors(issues)
    assert warnings(issues) == ["Email không ghi nhà vận chuyển"]


# --- Ca sát giới hạn --------------------------------------------------------

def test_arrival_before_opening_is_warning():
    _, issues = check(good_output(earliest_start="2026-10-21T07:00:00+07:00"))
    assert not has_errors(issues)
    assert "trước giờ mở cửa 08:00" in warnings(issues)[0]


def test_arrival_exactly_at_closing_is_warning():
    _, issues = check(good_output(earliest_start="2026-10-21T18:00:00+07:00"))
    assert "sau giờ đóng cửa 18:00" in warnings(issues)[0]


def test_duration_longer_than_working_day_is_warning():
    _, issues = check(good_output(duration_minutes=11 * 60))
    assert "dài hơn cả ngày" in warnings(issues)[0]


def test_duration_one_minute_is_ok():
    _, issues = check(good_output(duration_minutes=1))
    assert issues == []


# --- Dữ liệu sai ------------------------------------------------------------

@pytest.mark.parametrize("bad", [0, -30])
def test_duration_zero_or_negative_is_error(bad):
    parsed, issues = check(good_output(duration_minutes=bad))
    assert parsed is None
    assert errors(issues) == {"duration_minutes"}


def test_wrong_request_id_is_error():
    parsed, issues = check(good_output(request_id="R99"))
    assert parsed is not None  # đúng khuôn...
    assert errors(issues) == {"request_id"}  # ...nhưng sai nội dung


@pytest.mark.parametrize("bad", ["frozen", "STANDARD", None])
def test_unknown_dock_type_is_error(bad):
    _, issues = check(good_output(dock_type=bad))
    assert errors(issues) == {"dock_type"}


def test_unknown_missing_field_name_is_error():
    _, issues = check(good_output(missing_fields=["cargo_type"]))
    assert errors(issues) == {"missing_fields.0"}


def test_invalid_datetime_string_is_error():
    _, issues = check(good_output(earliest_start="9 giờ sáng"))
    assert errors(issues) == {"earliest_start"}


def test_extra_field_guessed_by_genai_is_error():
    _, issues = check(good_output(cargo_type="rau củ", estimated_duration=45))
    assert errors(issues) == {"cargo_type", "estimated_duration"}


def test_non_object_output_is_error():
    _, issues = check(["R01"])
    assert has_errors(issues)


# --- Request của API --------------------------------------------------------

def test_extract_request_needs_only_email():
    req = ExtractRequest(email="Xe tôi đến từ 9 giờ, cần một tiếng để dỡ hàng.")
    assert req.received_at is None


def test_extract_request_rejects_received_at_without_timezone():
    with pytest.raises(ValidationError):
        ExtractRequest(email="...", received_at="2026-10-21T08:00:00")


def test_confirm_request_requires_start_with_timezone():
    with pytest.raises(ValidationError):
        ConfirmRequest(run_id=1, request=good_output(), dock_id="B", start="2026-10-21T10:15:00")
    ok = ConfirmRequest(run_id=1, request=good_output(), dock_id="B", start="2026-10-21T10:15:00+07:00")
    assert ok.start.tzinfo is not None
