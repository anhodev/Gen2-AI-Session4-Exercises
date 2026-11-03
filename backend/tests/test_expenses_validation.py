"""Test schema + kiểm tra kết quả GenAI của Bài 03. Không cần API key."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.core.llm import fill_placeholders, load_samples
from app.modules.expenses.schemas import ExpenseExtraction, ExtractRequest
from app.modules.expenses.validation import has_errors, validate_extraction

TODAY = date(2026, 10, 21)
SAMPLES = load_samples("expenses")


def good_output(**overrides) -> dict:
    """JSON mẫu trong đề, có thể đè từng ô để tạo ca sai."""
    data = {
        "submission_id": "E01",
        "vendor": "ABC Travel",
        "invoice_number": "INV-0042",
        "transaction_date": "2026-10-19",
        "amount_minor": 1250000,
        "currency": "VND",
        "receipt_id": "R-42",
        "missing_fields": [],
    }
    data.update(overrides)
    return data


def check(raw, submission_id="E01"):
    return validate_extraction(raw, submission_id, TODAY)


def errors(issues) -> set[str]:
    return {i.field for i in issues if i.level == "error"}


def warnings(issues) -> list[str]:
    return [i.message for i in issues if i.level == "warning"]


# --- Mẫu JSON gửi cho GenAI -------------------------------------------------

def test_json_schema_matches_exercise():
    schema = ExpenseExtraction.model_json_schema()
    props = schema["properties"]
    assert list(props) == [
        "submission_id", "vendor", "invoice_number", "transaction_date",
        "amount_minor", "currency", "receipt_id", "missing_fields",
    ]
    assert {"type": "null"} in props["receipt_id"]["anyOf"]  # được phép null
    assert {"type": "integer", "minimum": 0} in props["amount_minor"]["anyOf"]
    assert {"type": "string", "format": "date"} in props["transaction_date"]["anyOf"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(props)  # ô null vẫn phải ghi rõ


# --- Ca thường --------------------------------------------------------------

def test_exercise_output_is_valid():
    parsed, issues = check(good_output())
    assert issues == []
    assert parsed.transaction_date == date(2026, 10, 19)
    assert parsed.amount_minor == 1250000


def test_usd_amount_in_cents_is_valid():
    _, issues = check(good_output(currency="USD", amount_minor=12550))
    assert issues == []


def test_missing_codes_is_warning_not_error():
    # E03 trong đề: thiếu mã vẫn được tìm trùng, thuật toán sẽ trả needs_review
    parsed, issues = check(good_output(invoice_number=None, receipt_id=None, missing_fields=["invoice_number", "receipt_id"]))
    assert not has_errors(issues)
    assert any("cần người xem lại" in m for m in warnings(issues))
    assert parsed.receipt_id is None


def test_receipt_alone_is_enough_to_search():
    _, issues = check(good_output(vendor=None, invoice_number=None, missing_fields=["vendor", "invoice_number"]))
    assert not any("cần người xem lại" in m for m in warnings(issues))


def test_vendor_and_invoice_without_receipt_is_enough_to_search():
    _, issues = check(good_output(receipt_id=None, missing_fields=["receipt_id"]))
    assert issues == []


def test_text_is_kept_as_read():
    # Chuẩn hóa chữ hoa/dấu cách là việc của thuật toán, không sửa dữ liệu gốc ở đây
    parsed, _ = check(good_output(vendor="  abc   travel ", invoice_number="inv-0042"))
    assert (parsed.vendor, parsed.invoice_number) == ("  abc   travel ", "inv-0042")


def test_submission_id_comes_from_system():
    _, issues = check(good_output(submission_id="INV-0042"))
    assert errors(issues) == {"submission_id"}


# --- Ca sát giới hạn --------------------------------------------------------

def test_amount_zero_is_warning():
    _, issues = check(good_output(amount_minor=0))
    assert not has_errors(issues)
    assert "bằng 0" in warnings(issues)[0]


def test_transaction_today_is_ok_tomorrow_is_error():
    assert check(good_output(transaction_date="2026-10-21"))[1] == []
    _, issues = check(good_output(transaction_date="2026-10-22"))
    assert errors(issues) == {"transaction_date"}


def test_very_old_receipt_is_warning():
    _, issues = check(good_output(transaction_date="2025-10-01"))
    assert not has_errors(issues)
    assert "hơn 1 năm" in warnings(issues)[0]


def test_code_with_max_length_is_ok():
    assert check(good_output(receipt_id="R" * 64))[1] == []
    assert errors(check(good_output(receipt_id="R" * 65))[1]) == {"receipt_id"}


def test_missing_fields_must_match_null_fields():
    _, issues = check(good_output(amount_minor=None))  # null nhưng chưa ghi trong missing_fields
    assert "amount_minor đang null nhưng chưa ghi trong missing_fields" in warnings(issues)
    _, issues = check(good_output(missing_fields=["currency"]))  # ghi thiếu nhưng thật ra có giá trị
    assert "missing_fields ghi currency nhưng ô này đã có giá trị" in warnings(issues)


def test_missing_currency_is_warning_and_needs_review():
    _, issues = check(good_output(currency=None, missing_fields=["currency"]))
    assert not has_errors(issues)
    assert any("không tạo được nhãn" in m for m in warnings(issues))


# --- Dữ liệu sai ------------------------------------------------------------

@pytest.mark.parametrize("amount", [-1, -1250000])
def test_negative_amount_is_error(amount):
    parsed, issues = check(good_output(amount_minor=amount))
    assert parsed is None
    assert errors(issues) == {"amount_minor"}


@pytest.mark.parametrize("amount", [12.5, 1250000.0, "1250000", "1.250.000", True])
def test_amount_must_be_a_real_integer(amount):
    # Tiền phải là số nguyên ở đơn vị nhỏ nhất: không nhận số lẻ, chuỗi hay true/false
    _, issues = check(good_output(amount_minor=amount))
    assert errors(issues) == {"amount_minor"}


@pytest.mark.parametrize("currency", ["EUR", "JPY"])
def test_unsupported_currency_is_error(currency):
    _, issues = check(good_output(currency=currency))
    assert errors(issues) == {"currency"}
    assert "chưa được hỗ trợ" in issues[0].message


@pytest.mark.parametrize("currency", ["vnd", "đ", "₫", "US$", "VN"])
def test_currency_must_be_iso_code(currency):
    _, issues = check(good_output(currency=currency))
    assert errors(issues) == {"currency"}


@pytest.mark.parametrize("value", ["2026-13-01", "19/10/2026", "hôm qua"])
def test_bad_date_is_error(value):
    _, issues = check(good_output(transaction_date=value))
    assert errors(issues) == {"transaction_date"}


@pytest.mark.parametrize("field", ["vendor", "invoice_number", "receipt_id", "currency"])
def test_blank_string_must_be_null(field):
    _, issues = check(good_output(**{field: "   "}))
    assert field in errors(issues)


def test_code_without_letters_or_digits_is_error():
    _, issues = check(good_output(invoice_number="---"))
    assert errors(issues) == {"invoice_number"}


def test_extra_field_is_error():
    _, issues = check(good_output(status="unique"))  # GenAI tự kết luận -> không cho phép
    assert errors(issues) == {"status"}


def test_unknown_missing_field_is_error():
    _, issues = check(good_output(missing_fields=["amount"]))
    assert errors(issues) == {"missing_fields.0"}


def test_missing_required_field_is_error():
    raw = good_output()
    del raw["receipt_id"]  # phải ghi rõ null, không được bỏ ô
    _, issues = check(raw)
    assert errors(issues) == {"receipt_id"}


def test_non_object_output_is_error():
    _, issues = check("không phải JSON")
    assert has_errors(issues)


def test_extract_request_needs_text():
    with pytest.raises(ValidationError):
        ExtractRequest(text="")


# --- Chứng từ mẫu -----------------------------------------------------------

@pytest.mark.parametrize("sample", SAMPLES, ids=[s["label"][:7] for s in SAMPLES])
def test_every_sample_gives_expected_result(sample):
    raw = fill_placeholders(sample["llm_output"], {"submission_id": "E07"})
    _, issues = validate_extraction(raw, "E07", TODAY)
    assert has_errors(issues) is not sample["expect_can_check"]
    assert ("[Lỗi]" in sample["label"]) is not sample["expect_can_check"]
