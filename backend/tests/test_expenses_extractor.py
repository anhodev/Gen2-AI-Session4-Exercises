"""Test bước GenAI đọc chứng từ của Bài 03, dùng mock và monkeypatch. Không cần API key."""

from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import Settings
from app.core.llm import LLMResult, load_samples
from app.modules.expenses import extractor
from app.modules.expenses.extractor import build_user_prompt, extract_expense
from app.modules.expenses.pdf import MAX_PAGES, PdfTextError, pdf_to_text
from app.modules.expenses.schemas import ExtractRequest

from .pdf_factory import SAMPLE_LINES, SAMPLE_PDF, make_pdf

SAMPLES = load_samples("expenses")
MOCK = Settings(mock_llm=True)  # luôn chạy mock, không phụ thuộc file .env
TODAY = date(2026, 10, 21)


def sample(n: int) -> dict:
    return next(s for s in SAMPLES if s["label"].startswith(f"Mẫu {n} "))


def run(text: str, submission_id: str = "E07"):
    return extract_expense(ExtractRequest(text=text), submission_id, MOCK, TODAY)


# --- GenAI (mock) -----------------------------------------------------------

def test_samples_file_has_enough_cases():
    assert len(SAMPLES) >= 8


@pytest.mark.parametrize("item", SAMPLES, ids=[s["label"][:7] for s in SAMPLES])
def test_every_sample_gives_expected_result(item):
    response, result = run(item["input"])
    assert result.model == "mock"
    assert response.can_check is item["expect_can_check"]


def test_exercise_receipt_is_read_correctly():
    response, _ = run(sample(1)["input"], submission_id="E01")
    e = response.extraction
    assert response.submission_id == "E01"
    assert response.llm_output["submission_id"] == "E01"  # mock đã thay "{submission_id}"
    assert (e.vendor, e.invoice_number, e.receipt_id) == ("ABC Travel", "INV-0042", "R-42")
    assert (e.amount_minor, e.currency, e.transaction_date) == (1250000, "VND", date(2026, 10, 19))
    assert response.issues == []


def test_resubmission_keeps_text_as_read():
    # GenAI chép nguyên văn, chuẩn hóa chữ hoa/dấu cách để thuật toán làm
    e = run(sample(2)["input"])[0].extraction
    assert (e.vendor, e.invoice_number) == ("  abc   travel ", "inv-0042")


def test_missing_codes_sample_warns_needs_review():
    response, _ = run(sample(3)["input"])
    assert response.can_check is True
    assert any("cần người xem lại" in i.message for i in response.issues)


def test_negative_amount_sample_points_to_amount():
    response, _ = run(sample(8)["input"])
    assert response.extraction is None  # sai khuôn ở lớp 1
    assert [i.field for i in response.issues] == ["amount_minor"]


def test_eur_sample_points_to_currency():
    response, _ = run(sample(9)["input"])
    assert [i.field for i in response.issues if i.level == "error"] == ["currency"]


def test_genai_inventing_submission_id_is_error():
    response, _ = run(sample(10)["input"], submission_id="E10")
    assert response.llm_output["submission_id"] == "PL-99012"
    assert [i.field for i in response.issues] == ["submission_id"]


def test_unknown_text_in_mock_cannot_check():
    response, _ = run("Một chứng từ không có trong mẫu")
    assert response.can_check is False
    assert response.llm_output is None


def test_model_call_failure_blocks_check(monkeypatch):
    # Giả lập gọi model bị timeout
    monkeypatch.setattr(
        extractor, "extract_structured",
        lambda *args, **kwargs: LLMResult(raw=None, model="gpt-4o-mini", latency_ms=30_000, error="Gọi model thất bại: timeout"),
    )
    response, _ = extract_expense(ExtractRequest(text="ABC Travel 1.250.000đ"), "E09", MOCK, TODAY)
    assert response.can_check is False
    assert "timeout" in response.issues[0].message


def test_today_is_used_to_reject_future_date():
    # Cùng chứng từ ngày 19/10: nếu "hôm nay" là 18/10 thì ngày giao dịch ở tương lai
    response, _ = extract_expense(ExtractRequest(text=sample(1)["input"]), "E01", MOCK, date(2026, 10, 18))
    assert [i.field for i in response.issues if i.level == "error"] == ["transaction_date"]


def test_prompt_has_submission_id_today_and_document():
    prompt = build_user_prompt("ABC Travel\n1.250.000 VND", "E05", TODAY)
    assert "Mã chứng từ hệ thống đã cấp: E05" in prompt
    assert "Hôm nay: 2026-10-21" in prompt
    assert "<document>\nABC Travel\n1.250.000 VND\n</document>" in prompt


def test_system_prompt_keeps_hyphens_and_minor_units():
    assert "INV-0042 khác INV0042" in extractor.SYSTEM_PROMPT
    assert "12550" in extractor.SYSTEM_PROMPT


# --- Lấy chữ từ PDF ---------------------------------------------------------

def test_sample_pdf_gives_same_text_as_sample_7():
    text = pdf_to_text(SAMPLE_PDF.read_bytes())
    assert text == sample(7)["input"]
    response, _ = run(text)  # chế độ mock đọc được PDF mẫu
    assert (response.extraction.amount_minor, response.extraction.currency) == (12550, "USD")


def test_sample_pdf_is_up_to_date():
    assert SAMPLE_PDF.read_bytes() == make_pdf([SAMPLE_LINES])


def test_multi_page_pdf_joins_pages():
    text = pdf_to_text(make_pdf([["Page one ABC Travel"], ["Page two Total 1.250.000 VND"]]))
    assert text == "Page one ABC Travel\n\nPage two Total 1.250.000 VND"


def test_scanned_pdf_without_text_is_error():
    with pytest.raises(PdfTextError, match="không có lớp chữ"):
        pdf_to_text(make_pdf([[]]))


def test_not_a_pdf_is_error():
    with pytest.raises(PdfTextError, match="không phải PDF"):
        pdf_to_text(b"\x89PNG\r\n fake image")


def test_broken_pdf_is_error():
    with pytest.raises(PdfTextError, match="Không đọc được PDF"):
        pdf_to_text(b"%PDF-1.4\n garbage without objects")


def test_password_protected_pdf_is_error():
    writer = PdfWriter(clone_from=PdfReader(BytesIO(SAMPLE_PDF.read_bytes())))
    writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    with pytest.raises(PdfTextError, match="mật khẩu"):
        pdf_to_text(buffer.getvalue())


def test_too_many_pages_is_error():
    with pytest.raises(PdfTextError, match="tối đa"):
        pdf_to_text(make_pdf([["ABC Travel INV-0042"]] * (MAX_PAGES + 1)))
