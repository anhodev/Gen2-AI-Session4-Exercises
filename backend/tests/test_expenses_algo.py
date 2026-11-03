"""Test thuật toán Bài 03. Không gọi GenAI, không cần database.

Chia 3 nhóm theo yêu cầu nộp bài: ca thường, ca sát giới hạn, dữ liệu sai.
"""

import random
import time
from datetime import date

import pytest

from app.modules.expenses.algo import (
    DUPLICATE,
    NEEDS_REVIEW,
    UNIQUE,
    DuplicateSubmissionError,
    Expense,
    ExpenseError,
    ExpenseIndex,
    InvalidAmountError,
    UnsupportedCurrencyError,
    format_amount,
    lookup_keys,
    normalize_text,
)

DAY = date(2026, 10, 19)


def exp(submission_id, vendor="ABC Travel", invoice="INV-0042", receipt="R-42",
        amount=1250000, currency="VND", day=DAY) -> Expense:
    return Expense(submission_id, vendor, invoice, day, amount, currency, receipt)


def exercise_samples() -> list[Expense]:
    """Ba chứng từ trong đề."""
    return [
        exp("E01"),
        exp("E02", vendor="abc travel", invoice="inv-0042"),
        exp("E03", invoice=None, receipt=None),
    ]


def run_all(expenses) -> dict:
    index = ExpenseIndex()
    return {e.submission_id: index.check_and_add(e) for e in expenses}


def matched_ids(result) -> list[str]:
    return [m.submission_id for m in result.matches]


# --- Ca thường --------------------------------------------------------------

def test_exercise_samples_give_three_statuses():
    results = run_all(exercise_samples())
    assert results["E01"].status == UNIQUE
    assert results["E02"].status == DUPLICATE
    assert matched_ids(results["E02"]) == ["E01"]
    assert results["E03"].status == NEEDS_REVIEW


def test_unique_reason_is_not_a_guarantee():
    reason = run_all([exp("E01")])["E01"].reason
    assert "quy tắc hiện tại" in reason
    assert "không phải kết luận" in reason


def test_case_and_extra_spaces_still_match():
    results = run_all([exp("E01"), exp("E02", vendor="  ABC   TRAVEL ", invoice=" inv-0042", receipt="r-42 ")])
    assert results["E02"].matches[0].matched_by == ["receipt", "invoice"]
    notes = {d.field: d.note for d in results["E02"].matches[0].differences}
    assert notes == {
        "vendor": "chỉ khác chữ hoa/dấu cách",
        "invoice_number": "chỉ khác chữ hoa/dấu cách",
        "receipt_id": "chỉ khác chữ hoa/dấu cách",
    }


def test_different_amount_is_still_flagged_with_gap():
    # Đề: E02 có số tiền khác vẫn đánh dấu vì mã biên nhận trùng, và hiển thị chênh lệch
    results = run_all([exp("E01"), exp("E02", amount=1350000)])
    assert results["E02"].status == DUPLICATE
    diff = results["E02"].matches[0].differences[0]
    assert (diff.field, diff.current, diff.previous, diff.note) == (
        "amount_minor", "1.350.000 VND", "1.250.000 VND", "chênh +100.000 VND",
    )
    assert "chênh +100.000 VND" in results["E02"].reason


def test_resubmission_without_receipt_found_by_vendor_and_invoice():
    # Nhãn thứ hai giúp tìm bản gửi sau bị thiếu mã biên nhận
    results = run_all([exp("E01"), exp("E05", receipt=None)])
    assert results["E05"].status == DUPLICATE
    assert results["E05"].matches[0].matched_by == ["invoice"]


def test_same_receipt_different_invoice_is_flagged():
    results = run_all([exp("E01"), exp("E02", invoice="INV-9999")])
    assert results["E02"].matches[0].matched_by == ["receipt"]


def test_check_does_not_add():
    index = ExpenseIndex()
    index.check(exp("E01"))
    assert len(index) == 0
    assert index.check(exp("E02")).status == UNIQUE


def test_third_copy_matches_both_earlier_ones_in_order():
    results = run_all([exp("E01"), exp("E02"), exp("E03")])
    assert matched_ids(results["E03"]) == ["E01", "E02"]


def test_usd_receipt_is_separate_from_vnd():
    results = run_all([exp("E01"), exp("E02", currency="USD", amount=5000)])
    assert results["E02"].status == UNIQUE  # cùng mã nhưng khác loại tiền


def test_format_amount():
    assert format_amount(1250000, "VND") == "1.250.000 VND"
    assert format_amount(12550, "USD") == "125.50 USD"
    assert format_amount(5, "USD") == "0.05 USD"
    assert format_amount(None, "VND") is None


# --- Ca sát giới hạn --------------------------------------------------------

def test_hyphen_is_kept_inv0042_is_not_inv_0042():
    # Không có mã biên nhận trùng -> INV-0042 và INV0042 là hai mã khác nhau
    results = run_all([exp("E01", receipt=None), exp("E06", invoice="INV0042", receipt=None)])
    assert results["E06"].status == UNIQUE


def test_hyphen_difference_still_flagged_when_receipt_matches():
    results = run_all([exp("E01"), exp("E06", invoice="INV0042")])
    assert results["E06"].status == DUPLICATE
    match = results["E06"].matches[0]
    assert match.matched_by == ["receipt"]
    assert match.differences[0].note == ""  # khác thật, không phải chỉ khác chữ hoa


def test_unicode_composed_and_decomposed_are_same():
    composed = "Phúc Long"                       # "ú" là 1 ký tự
    decomposed = "Phúc Long"               # "u" + dấu sắc rời
    assert composed != decomposed
    assert normalize_text(composed) == normalize_text(decomposed)
    results = run_all([exp("E01", vendor=composed, receipt=None), exp("E02", vendor=decomposed, receipt=None)])
    assert results["E02"].status == DUPLICATE


def test_fullwidth_and_tabs_are_normalized():
    assert normalize_text("ＩＮＶ-0042") == "inv-0042"
    assert normalize_text("ABC\tTravel\n") == "abc travel"
    assert normalize_text("   ") is None


def test_only_vendor_amount_and_date_is_not_enough():
    # Đề: chỉ cùng nơi bán, số tiền và ngày chưa đủ để gọi là trùng
    results = run_all([exp("E01"), exp("E03", invoice=None, receipt=None)])
    assert results["E03"].status == NEEDS_REVIEW
    assert results["E03"].matches == []
    assert "chưa đủ để gọi là trùng" in results["E03"].reason


def test_missing_currency_needs_review():
    result = run_all([exp("E01", currency=None)])["E01"]
    assert (result.status, result.keys) == (NEEDS_REVIEW, [])
    assert "loại tiền" in result.reason


def test_invoice_without_vendor_has_receipt_key_only():
    assert lookup_keys(exp("E01", vendor=None)) == [("receipt", "VND", "r-42")]
    assert lookup_keys(exp("E01", vendor=None, receipt=None)) == []


def test_separator_inside_text_cannot_fake_a_match():
    # Nhãn là tuple, không nối chuỗi: "a|b" + "c" khác "a" + "b|c"
    a = lookup_keys(exp("E01", vendor="A|B", invoice="C", receipt=None))
    b = lookup_keys(exp("E02", vendor="A", invoice="B|C", receipt=None))
    assert a != b


def test_recheck_after_editing_receipt():
    # Người dùng sửa mã biên nhận -> bỏ bản cũ khỏi bảng, kiểm tra lại
    index = ExpenseIndex()
    index.check_and_add(exp("E01", invoice="INV-1"))
    assert index.check_and_add(exp("E02", invoice="INV-2")).status == DUPLICATE  # cùng R-42
    index.remove("E02")
    assert index.check_and_add(exp("E02", invoice="INV-2", receipt="R-43")).status == UNIQUE
    assert index.check(exp("E09", invoice="INV-9", receipt="R-42")).matches[0].submission_id == "E01"


def test_document_does_not_match_itself():
    index = ExpenseIndex()
    index.add(exp("E01"))
    assert index.check(exp("E01")).status == UNIQUE


def test_remove_unknown_returns_none():
    assert ExpenseIndex().remove("E99") is None


# --- Dữ liệu sai ------------------------------------------------------------

def test_negative_amount_is_rejected():
    with pytest.raises(InvalidAmountError):
        exp("E01", amount=-1)


@pytest.mark.parametrize("amount", [12.5, "1250000", True])
def test_amount_must_be_integer(amount):
    with pytest.raises(InvalidAmountError):
        exp("E01", amount=amount)


@pytest.mark.parametrize("currency", ["EUR", "vnd", ""])
def test_unsupported_currency_is_rejected(currency):
    with pytest.raises(UnsupportedCurrencyError):
        exp("E01", currency=currency)


def test_empty_submission_id_is_rejected():
    with pytest.raises(ExpenseError):
        exp(" ")


def test_adding_same_submission_twice_is_rejected():
    index = ExpenseIndex()
    index.add(exp("E01"))
    with pytest.raises(DuplicateSubmissionError):
        index.add(exp("E01", receipt="R-99"))


# --- Đối chiếu brute force và tốc độ ----------------------------------------

def brute_force(expenses: list[Expense]) -> dict[str, tuple[str, list[str]]]:
    """So từng cặp O(n^2), dùng để đối chiếu với hash map."""
    out = {}
    for i, current in enumerate(expenses):
        keys = set(lookup_keys(current))
        if not keys:
            out[current.submission_id] = (NEEDS_REVIEW, [])
            continue
        olds = [e.submission_id for e in expenses[:i] if keys & set(lookup_keys(e))]
        out[current.submission_id] = (DUPLICATE if olds else UNIQUE, olds)
    return out


@pytest.mark.parametrize("seed", range(20))
def test_matches_brute_force_on_random_data(seed):
    rng = random.Random(seed)
    vendors = ["ABC Travel", "abc  travel", "Xanh SM", None]
    invoices = ["INV-0042", "inv-0042", "INV0042", "INV-7", None]
    receipts = ["R-42", "r-42", "R-43", None]
    expenses = [
        exp(f"E{i:03d}", rng.choice(vendors), rng.choice(invoices), rng.choice(receipts),
            rng.randrange(0, 10**7), rng.choice(["VND", "USD", None]))
        for i in range(60)
    ]
    results = run_all(expenses)
    assert {k: (r.status, matched_ids(r)) for k, r in results.items()} == brute_force(expenses)


def test_10000_expenses_run_fast():
    rng = random.Random(1)
    expenses = [
        exp(f"E{i:05d}", f"Vendor {rng.randrange(500)}", f"INV-{rng.randrange(5000)}", f"R-{rng.randrange(8000)}")
        for i in range(10_000)
    ]
    started = time.perf_counter()
    results = run_all(expenses)
    assert time.perf_counter() - started < 1.0  # O(n): khoảng vài chục ms
    assert sum(r.status == DUPLICATE for r in results.values()) > 0
