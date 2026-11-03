"""Test API Bài 03 bằng TestClient. Mock GenAI + SQLite tạm, không cần mạng."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.llm import load_samples
from app.main import create_app

from .pdf_factory import SAMPLE_PDF, make_pdf

# Chứng từ mẫu, đánh chỉ mục theo số thứ tự: 1 = "Mẫu 1 - ..."
SAMPLES = {i: s for i, s in enumerate(load_samples("expenses"), start=1)}


def make_client(db_url: str) -> TestClient:
    return TestClient(create_app(Settings(database_url=db_url, mock_llm=True)))


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path}/api.db"


@pytest.fixture
def client(db_url):
    # "with" để chạy lifespan: tạo bảng + dựng lại bảng tra cứu
    with make_client(db_url) as c:
        yield c


def extract(client, n: int) -> dict:
    response = client.post("/expenses/extract", json={"text": SAMPLES[n]["input"]})
    assert response.status_code == 200, response.text
    return response.json()


def check(client, data: dict, **edits):
    expense = {**data["llm_output"], **edits}
    return client.post("/expenses/check", json={"run_id": data["run_id"], "expense": expense})


def submit(client, n: int, **edits) -> dict:
    """Extract mẫu thứ n rồi kiểm tra trùng (có thể sửa vài ô trước)."""
    response = check(client, extract(client, n), **edits)
    assert response.status_code == 200, response.text
    return response.json()


def edited(submission_id: str, **overrides) -> dict:
    """Thông tin chứng từ E01 trong đề, gán mã khác và sửa vài ô, để gửi kèm khi kiểm tra lại."""
    data = {
        "submission_id": submission_id, "vendor": "ABC Travel", "invoice_number": "INV-0042",
        "transaction_date": "2026-10-19", "amount_minor": 1250000, "currency": "VND",
        "receipt_id": "R-42", "missing_fields": [],
    }
    return {**data, **overrides}


def review(client, submission_id: str, action: str, **extra):
    return client.post("/expenses/review", json={"submission_id": submission_id, "action": action, **extra})


# --- Ca thường --------------------------------------------------------------

def test_samples_endpoint(client):
    samples = client.get("/expenses/samples").json()
    assert len(samples) == len(SAMPLES)
    assert samples[0]["label"].startswith("Mẫu 1 ")
    assert samples[0]["text"] == SAMPLES[1]["input"]


def test_extract_generates_submission_id_and_saves_run(client):
    data = extract(client, 1)
    assert data["submission_id"] == "E01"
    assert data["llm_output"]["submission_id"] == "E01"
    assert data["can_check"] is True
    run = client.get(f"/runs/{data['run_id']}").json()
    assert (run["module"], run["status"], run["raw_input"]["submission_id"]) == ("expenses", "extracted", "E01")


def test_submission_id_from_browser_is_ignored(client):
    data = client.post("/expenses/extract", json={"text": SAMPLES[1]["input"], "submission_id": "HACK"}).json()
    assert data["submission_id"] == "E01"


def test_extract_does_not_check(client):
    extract(client, 1)
    assert client.get("/expenses/submissions").json() == []


def test_exercise_samples_give_three_statuses(client):
    # Tự kiểm tra 1: E01 chưa trùng, E02 trùng E01, E03 cần người xem lại
    e01, e02, e03 = submit(client, 1), submit(client, 2), submit(client, 3)
    assert [r["submission_id"] for r in (e01, e02, e03)] == ["E01", "E02", "E03"]
    assert e01["status"] == "unique_under_current_rules"
    assert e02["status"] == "duplicate_candidate"
    assert e03["status"] == "needs_review"
    assert [m["submission_id"] for m in e02["matches"]] == ["E01"]
    assert (e01["review_status"], e02["review_status"], e03["review_status"]) == (None, "pending", "pending")


def test_duplicate_shows_old_document_and_differences(client):
    submit(client, 1)
    e02 = submit(client, 2)
    match = e02["matches"][0]
    assert match["matched_by"] == ["receipt", "invoice"]
    assert match["previous"]["vendor"] == "ABC Travel"
    assert match["previous"]["amount_text"] == "1.250.000 VND"
    notes = {d["field"]: d["note"] for d in match["differences"]}
    assert notes == {"vendor": "chỉ khác chữ hoa/dấu cách", "invoice_number": "chỉ khác chữ hoa/dấu cách"}


def test_different_amount_still_flagged_with_gap(client):
    submit(client, 1)
    result = submit(client, 4)
    assert result["status"] == "duplicate_candidate"
    assert "chênh +100.000 VND" in result["reason"]


def test_resubmission_without_receipt_found_by_invoice(client):
    submit(client, 1)
    result = submit(client, 5)
    assert result["matches"][0]["matched_by"] == ["invoice"]


def test_run_keeps_before_and_after_user_edit(client):
    data = extract(client, 1)
    check(client, data, receipt_id="R-420")  # người dùng sửa mã biên nhận trước khi kiểm tra
    run = client.get(f"/runs/{data['run_id']}").json()
    assert run["status"] == "confirmed"
    assert (run["llm_output"]["receipt_id"], run["user_output"]["receipt_id"]) == ("R-42", "R-420")
    assert run["result"]["edited_fields"] == ["receipt_id"]
    assert run["result"]["status"] == "unique_under_current_rules"


def test_usd_receipt(client):
    result = submit(client, 7)
    assert result["status"] == "unique_under_current_rules"
    assert client.get("/expenses/submissions").json()[0]["amount_text"] == "125.50 USD"


# --- Chữ hoa, dấu cách, dấu gạch nối (Tự kiểm tra 2) -------------------------

def test_case_and_spaces_do_not_hide_duplicate(client):
    submit(client, 1)
    result = submit(client, 1, vendor="  ABC   TRAVEL ", invoice_number="inv-0042", receipt_id="r-42")
    assert result["status"] == "duplicate_candidate"


def test_inv0042_is_not_inv_0042_without_same_receipt(client):
    submit(client, 1, receipt_id=None, missing_fields=["receipt_id"])
    assert submit(client, 6)["status"] == "unique_under_current_rules"


# --- Dữ liệu chưa đủ tin cậy: không trả "chưa trùng" (Tự kiểm tra 3) ---------

def test_missing_codes_needs_review_not_unique(client):
    result = submit(client, 3)
    assert result["status"] == "needs_review"
    assert "chưa đủ để gọi là trùng" in result["reason"]


def test_negative_amount_cannot_be_checked(client):
    data = extract(client, 8)
    assert data["can_check"] is False
    assert data["extraction"] is None
    response = check(client, data)  # gửi thẳng số tiền âm
    assert response.status_code == 422
    assert client.get("/expenses/submissions").json() == []


def test_unsupported_currency_cannot_be_checked(client):
    data = extract(client, 9)
    assert data["can_check"] is False
    response = check(client, data)
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "currency"
    assert client.get("/expenses/submissions").json() == []


def test_unsupported_currency_fixed_by_user_can_be_checked(client):
    # Người dùng đổi tay sang VND (đã quy đổi) thì kiểm tra được
    assert submit(client, 9, currency="VND", amount_minor=520000)["status"] == "unique_under_current_rules"


def test_future_date_cannot_be_checked(client):
    response = check(client, extract(client, 1), transaction_date="2099-01-01")
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "transaction_date"


def test_missing_currency_needs_review(client):
    result = submit(client, 1, currency=None, missing_fields=["currency"])
    assert result["status"] == "needs_review"
    assert result["keys"] == []


@pytest.mark.parametrize("amount", [12.5, "1250000"])
def test_amount_must_be_integer(client, amount):
    assert check(client, extract(client, 1), amount_minor=amount).status_code == 422


def test_genai_invented_submission_id_is_400(client):
    data = extract(client, 10)
    assert data["can_check"] is False
    response = check(client, data)
    assert response.status_code == 400
    assert response.json()["detail"][0]["field"] == "submission_id"


def test_genai_invented_id_fixed_with_system_id(client):
    data = extract(client, 10)
    assert check(client, data, submission_id=data["submission_id"]).status_code == 200


def test_tampered_submission_id_is_400(client):
    submit(client, 1)
    response = check(client, extract(client, 2), submission_id="E01")
    assert response.status_code == 400


def test_check_twice_is_409(client):
    data = extract(client, 1)
    assert check(client, data).status_code == 200
    assert check(client, data).status_code == 409


def test_unknown_run_is_404(client):
    data = extract(client, 1)
    assert client.post("/expenses/check", json={"run_id": 999, "expense": data["llm_output"]}).status_code == 404


def test_ticket_run_cannot_be_used_for_expenses(client):
    ticket = load_samples("tickets")[0]
    run_id = client.post("/tickets/extract", json={"received_at": ticket["received_at"], "message": ticket["input"]}).json()["run_id"]
    data = extract(client, 1)
    assert client.post("/expenses/check", json={"run_id": run_id, "expense": data["llm_output"]}).status_code == 404


# --- Người duyệt ------------------------------------------------------------

def test_confirm_duplicate(client):
    submit(client, 1)
    submit(client, 2)
    body = review(client, "E02", "confirm_duplicate", note="cùng vé").json()
    assert body["submission"]["review_status"] == "confirmed_duplicate"
    assert body["result"] is None
    detail = client.get("/expenses/submissions/E02").json()
    assert [(r["action"], r["note"]) for r in detail["reviews"]] == [("confirm_duplicate", "cùng vé")]


def test_dismiss_suspicion(client):
    submit(client, 1)
    submit(client, 2)
    assert review(client, "E02", "dismiss").json()["submission"]["review_status"] == "dismissed"


def test_editing_receipt_requires_recheck(client):
    # Đề: nếu sửa mã biên nhận, phải kiểm tra trùng lại
    submit(client, 1)
    assert submit(client, 4)["status"] == "duplicate_candidate"  # E02 trùng R-42 với E01
    fixed = edited("E02", receipt_id="R-43", invoice_number="INV-0043", amount_minor=1350000)

    # Gửi bản sửa kèm "bỏ nghi vấn" -> bị chặn, phải kiểm tra lại
    assert review(client, "E02", "dismiss", expense=fixed).status_code == 400

    body = review(client, "E02", "recheck", expense=fixed, note="mã đúng là R-43").json()
    assert body["result"]["status"] == "unique_under_current_rules"
    assert (body["submission"]["receipt_id"], body["submission"]["review_status"]) == ("R-43", None)

    log = client.get("/expenses/submissions/E02").json()["reviews"][-1]
    assert (log["action"], log["before"]["receipt_id"], log["after"]["receipt_id"]) == ("recheck", "R-42", "R-43")


def test_recheck_can_find_new_duplicate(client):
    submit(client, 1)
    e07 = submit(client, 7)  # USD, chưa trùng
    assert e07["status"] == "unique_under_current_rules"
    body = review(client, "E02", "recheck", expense=edited("E02")).json()  # sửa thành giống E01
    assert body["result"]["status"] == "duplicate_candidate"
    assert body["submission"]["review_status"] == "pending"


def test_recheck_with_bad_data_is_400_and_keeps_old(client):
    submit(client, 1)
    bad = edited("E01", amount_minor=1840, currency="EUR")
    assert review(client, "E01", "recheck", expense=bad).status_code == 400
    assert client.get("/expenses/submissions/E01").json()["submission"]["currency"] == "VND"


def test_recheck_without_expense_is_400(client):
    submit(client, 1)
    assert review(client, "E01", "recheck").status_code == 400


def test_recheck_other_submission_id_is_400(client):
    submit(client, 1)
    submit(client, 2)
    assert review(client, "E02", "recheck", expense=edited("E01")).status_code == 400


def test_nothing_to_review_for_unique_is_409(client):
    submit(client, 1)
    assert review(client, "E01", "dismiss").status_code == 409


def test_confirm_duplicate_without_match_is_409(client):
    submit(client, 3)
    assert review(client, "E01", "confirm_duplicate").status_code == 409


def test_review_unknown_submission_is_404(client):
    assert review(client, "E99", "dismiss").status_code == 404


def test_unknown_action_is_422(client):
    # Hệ thống không có thao tác tự từ chối hoàn tiền
    assert review(client, "E01", "reject_refund").status_code == 422


# --- PDF --------------------------------------------------------------------

def test_sample_pdf_endpoint_and_text(client):
    pdf = client.get("/expenses/sample-pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    body = client.post("/expenses/pdf-text", files={"file": ("hotel.pdf", pdf.content, "application/pdf")}).json()
    assert body == {"filename": "hotel.pdf", "text": SAMPLES[7]["input"]}
    # Văn bản lấy từ PDF cho GenAI (mock) đọc được luôn
    data = client.post("/expenses/extract", json={"text": body["text"]}).json()
    assert (data["extraction"]["amount_minor"], data["extraction"]["currency"]) == (12550, "USD")


def test_scanned_pdf_is_400(client):
    response = client.post("/expenses/pdf-text", files={"file": ("scan.pdf", make_pdf([[]]), "application/pdf")})
    assert response.status_code == 400
    assert "không có lớp chữ" in response.json()["detail"]


def test_not_a_pdf_is_400(client):
    response = client.post("/expenses/pdf-text", files={"file": ("photo.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")})
    assert response.status_code == 400


def test_too_large_pdf_is_400(client):
    big = SAMPLE_PDF.read_bytes() + b"%" * (5 * 1024 * 1024)
    response = client.post("/expenses/pdf-text", files={"file": ("big.pdf", big, "application/pdf")})
    assert response.status_code == 400
    assert "lớn hơn" in response.json()["detail"]


# --- Danh sách và khởi động lại ---------------------------------------------

def test_submissions_list_newest_first(client):
    submit(client, 1)
    submit(client, 2)
    rows = client.get("/expenses/submissions?limit=5").json()
    assert [r["submission_id"] for r in rows] == ["E02", "E01"]
    assert rows[0]["matched_ids"] == ["E01"]


def test_unchecked_submission_detail_is_404(client):
    extract(client, 1)
    assert client.get("/expenses/submissions/E01").status_code == 404


def test_index_survives_restart(db_url):
    with make_client(db_url) as c:
        submit(c, 1)
    with make_client(db_url) as c:  # khởi động lại app
        result = submit(c, 2)
        assert (result["submission_id"], result["status"]) == ("E02", "duplicate_candidate")
