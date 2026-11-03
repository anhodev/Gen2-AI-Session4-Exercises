"""Bài 03 - Thuật toán tìm chứng từ có thể đã được gửi trước đó.

  - Chuẩn hóa trước khi so: Unicode NFKC, bỏ dấu cách thừa, không phân biệt hoa/thường.
      KHÔNG xóa dấu gạch nối: INV-0042 và INV0042 có thể là hai mã khác nhau.
  - Mỗi chứng từ có tối đa 2 nhãn tra cứu:
      1. loại tiền + mã biên nhận
      2. loại tiền + nơi bán + số hóa đơn (tìm được bản gửi sau bị thiếu mã biên nhận)
  - Hash map: nhãn -> các chứng từ cũ có nhãn đó. Tra một nhãn O(1) trung bình,
      n chứng từ khoảng O(n).
  - Kết quả:
      duplicate_candidate         có nhãn đã gặp: cho biết chứng từ cũ và các ô khác nhau
      needs_review                không tạo được nhãn nào (thiếu mã, thiếu loại tiền)
      unique_under_current_rules  chưa thấy nhãn này. KHÔNG có nghĩa là chắc chắn hợp lệ.
  - Hệ thống chỉ giúp người duyệt kiểm tra, không tự kết luận gian lận hay từ chối hoàn tiền.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

# Loại tiền app hỗ trợ và số chữ số lẻ của đơn vị nhỏ nhất:
# VND không có số lẻ (1 đồng), USD có 2 (1 cent = 0,01 USD).
MINOR_DIGITS = {"VND": 0, "USD": 2}

DUPLICATE = "duplicate_candidate"
NEEDS_REVIEW = "needs_review"
UNIQUE = "unique_under_current_rules"

# Tên nhãn, dùng trong lý do hiển thị cho người duyệt
KEY_LABELS = {"receipt": "mã biên nhận", "invoice": "nơi bán + số hóa đơn"}


# Lỗi: mỗi loại một class để API trả thông báo rõ ràng
class ExpenseError(ValueError):
    """Lỗi chung của module chứng từ."""


class UnsupportedCurrencyError(ExpenseError):
    """Loại tiền chưa được hỗ trợ."""


class InvalidAmountError(ExpenseError):
    """Số tiền không phải số nguyên không âm."""


class DuplicateSubmissionError(ExpenseError):
    """Mã chứng từ đã có trong bảng tra cứu."""


@dataclass(frozen=True)
class Expense:
    """Chứng từ đã được người dùng xem/sửa (GenAI đọc, code kiểm tra)."""

    submission_id: str
    vendor: str | None
    invoice_number: str | None
    transaction_date: date | None
    amount_minor: int | None
    currency: str | None
    receipt_id: str | None

    def __post_init__(self) -> None:
        # Kiểm tra lại dù validation đã làm: gọi thẳng thuật toán với dữ liệu sai cũng không ra "chưa trùng"
        if not self.submission_id or not self.submission_id.strip():
            raise ExpenseError("submission_id không được rỗng")
        if self.currency is not None and self.currency not in MINOR_DIGITS:
            raise UnsupportedCurrencyError(f"Loại tiền '{self.currency}' chưa được hỗ trợ. Chỉ nhận: {', '.join(MINOR_DIGITS)}")
        if self.amount_minor is not None:
            if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
                raise InvalidAmountError("amount_minor phải là số nguyên (đơn vị nhỏ nhất)")
            if self.amount_minor < 0:
                raise InvalidAmountError(f"amount_minor không được âm, nhận {self.amount_minor}")


@dataclass(frozen=True)
class FieldDiff:
    """Một ô khác nhau giữa chứng từ mới và chứng từ cũ."""

    field: str
    current: str | None    # giá trị của chứng từ đang kiểm tra (đã định dạng để đọc)
    previous: str | None   # giá trị của chứng từ cũ
    note: str = ""         # vd "chỉ khác chữ hoa/dấu cách", "chênh +100.000 VND"


@dataclass(frozen=True)
class Match:
    """Một chứng từ cũ có chung nhãn tra cứu."""

    submission_id: str
    matched_by: list[str]          # "receipt", "invoice" hoặc cả hai
    differences: list[FieldDiff]


@dataclass(frozen=True)
class CheckResult:
    status: str                    # DUPLICATE | NEEDS_REVIEW | UNIQUE
    keys: list[tuple]              # các nhãn tra cứu của chứng từ này
    matches: list[Match] = field(default_factory=list)
    reason: str = ""


# --- Chuẩn hóa và nhãn ------------------------------------------------------

def normalize_text(value: str | None) -> str | None:
    """
    Chuẩn hóa để so sánh: NFKC, gộp mọi khoảng trắng thành 1 dấu cách, bỏ ở 2 đầu, chữ thường.

    NFKC gộp các cách viết khác nhau của cùng một ký tự: "ú" dựng sẵn và "u" + dấu sắc,
    chữ toàn khổ "ＩＮＶ" và "INV". Không xóa dấu gạch nối hay ký tự nào khác.

    Returns:
        str | None: Chuỗi đã chuẩn hóa, None nếu rỗng.
    """
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return text or None


def lookup_keys(expense: Expense) -> list[tuple]:
    """
    Tạo nhãn tra cứu cho một chứng từ. Không có loại tiền thì không tạo nhãn nào:
    cùng mã nhưng khác loại tiền là hai chứng từ khác nhau.

    Returns:
        list[tuple]: 0, 1 hoặc 2 nhãn. Ví dụ ("receipt", "VND", "r-42"),
        ("invoice", "VND", "abc travel", "inv-0042").
    """
    if expense.currency is None:
        return []
    keys = []
    receipt = normalize_text(expense.receipt_id)
    if receipt:
        keys.append(("receipt", expense.currency, receipt))
    vendor, invoice = normalize_text(expense.vendor), normalize_text(expense.invoice_number)
    if vendor and invoice:
        keys.append(("invoice", expense.currency, vendor, invoice))
    return keys


# --- Bảng tra cứu (hash map) ------------------------------------------------

class ExpenseIndex:
    """
    Bảng tra cứu chứng từ đã gửi: nhãn -> danh sách mã chứng từ (theo thứ tự đã thêm).

    Thêm, xóa, tra một chứng từ: O(1) trung bình (mỗi chứng từ có tối đa 2 nhãn).
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple, list[str]] = defaultdict(list)
        self._expenses: dict[str, Expense] = {}
        self._order: dict[str, int] = {}  # thứ tự đã thêm, để bản gửi sớm nhất đứng đầu
        self._counter = 0

    def __len__(self) -> int:
        return len(self._expenses)

    def __contains__(self, submission_id: str) -> bool:
        return submission_id in self._expenses

    def get(self, submission_id: str) -> Expense | None:
        return self._expenses.get(submission_id)

    def add(self, expense: Expense) -> None:
        """
        Thêm chứng từ vào bảng. Chứng từ trùng cũng được thêm, để bản gửi lần 3 tìm ra cả hai bản trước.

        Raises:
            DuplicateSubmissionError: Mã chứng từ đã có trong bảng.
        """
        if expense.submission_id in self._expenses:
            raise DuplicateSubmissionError(f"Chứng từ {expense.submission_id} đã có trong bảng tra cứu")
        self._expenses[expense.submission_id] = expense
        self._counter += 1
        self._order[expense.submission_id] = self._counter
        for key in lookup_keys(expense):
            self._by_key[key].append(expense.submission_id)

    def remove(self, submission_id: str) -> Expense | None:
        """Bỏ chứng từ khỏi bảng, vd trước khi kiểm tra lại bản đã sửa mã biên nhận."""
        expense = self._expenses.pop(submission_id, None)
        if expense is None:
            return None
        del self._order[submission_id]
        for key in lookup_keys(expense):
            ids = self._by_key[key]
            ids.remove(submission_id)
            if not ids:
                del self._by_key[key]
        return expense

    def check(self, expense: Expense) -> CheckResult:
        """
        Tìm chứng từ cũ có chung nhãn. Chỉ đọc, không thêm chứng từ vào bảng.

        Args:
            expense (Expense): Chứng từ cần kiểm tra. Nếu đã có trong bảng thì bỏ qua chính nó.

        Returns:
            CheckResult: Trạng thái, nhãn, các chứng từ cũ trùng và lý do.
        """
        keys = lookup_keys(expense)
        if not keys:
            return CheckResult(NEEDS_REVIEW, [], [], explain_missing(expense))

        # Gom theo chứng từ cũ (một bản có thể trùng cả 2 nhãn)
        matched_by: dict[str, list[str]] = {}
        for key in keys:
            for old_id in self._by_key.get(key, []):
                if old_id != expense.submission_id:
                    matched_by.setdefault(old_id, []).append(key[0])

        if not matched_by:
            labels = ", ".join(KEY_LABELS[k[0]] for k in keys)
            return CheckResult(
                UNIQUE, keys, [],
                f"Chưa thấy chứng từ nào trùng {labels} theo quy tắc hiện tại. "
                "Đây không phải kết luận chứng từ chắc chắn hợp lệ.",
            )

        # Bản gửi sớm nhất đứng đầu
        matches = [
            Match(old_id, matched_by[old_id], compare(expense, self._expenses[old_id]))
            for old_id in sorted(matched_by, key=self._order.__getitem__)
        ]
        return CheckResult(DUPLICATE, keys, matches, explain_duplicate(expense, matches))

    def check_and_add(self, expense: Expense) -> CheckResult:
        """Kiểm tra rồi thêm vào bảng (dùng khi lưu chứng từ mới)."""
        result = self.check(expense)
        self.add(expense)
        return result


# --- So sánh và lý do -------------------------------------------------------

def format_amount(amount_minor: int | None, currency: str | None) -> str | None:
    """
    Định dạng số tiền để đọc: 1250000 VND -> "1.250.000 VND", 12550 USD -> "125.50 USD".
    """
    if amount_minor is None:
        return None
    digits = MINOR_DIGITS.get(currency or "", 0)
    if digits == 0:
        return f"{amount_minor:,} {currency or ''}".replace(",", ".").strip()
    major, minor = divmod(abs(amount_minor), 10 ** digits)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{major:,}.{minor:0{digits}d} {currency}"


def compare(current: Expense, previous: Expense) -> list[FieldDiff]:
    """
    Liệt kê các ô khác nhau giữa chứng từ mới và chứng từ cũ.

    Ô chữ khác cách viết nhưng giống nhau sau chuẩn hóa vẫn được liệt kê, kèm ghi chú,
    để người duyệt thấy vì sao hai bản được coi là cùng mã.
    """
    diffs = []
    for name in ("vendor", "invoice_number", "receipt_id"):
        a, b = getattr(current, name), getattr(previous, name)
        if a == b:
            continue
        same = a is not None and b is not None and normalize_text(a) == normalize_text(b)
        diffs.append(FieldDiff(name, a, b, "chỉ khác chữ hoa/dấu cách" if same else ""))

    if current.transaction_date != previous.transaction_date:
        diffs.append(FieldDiff(
            "transaction_date",
            current.transaction_date.isoformat() if current.transaction_date else None,
            previous.transaction_date.isoformat() if previous.transaction_date else None,
        ))

    if current.amount_minor != previous.amount_minor:
        note = ""
        if current.amount_minor is not None and previous.amount_minor is not None:
            gap = current.amount_minor - previous.amount_minor
            note = f"chênh {'+' if gap > 0 else '-'}{format_amount(abs(gap), current.currency)}"
        diffs.append(FieldDiff(
            "amount_minor",
            format_amount(current.amount_minor, current.currency),
            format_amount(previous.amount_minor, previous.currency),
            note,
        ))
    return diffs


def explain_missing(expense: Expense) -> str:
    """Lý do không tạo được nhãn tra cứu."""
    if expense.currency is None:
        missing = "loại tiền"
    else:
        lacking = [label for name, label in (("vendor", "nơi bán"), ("invoice_number", "số hóa đơn"))
                   if not normalize_text(getattr(expense, name))]
        missing = "mã biên nhận và " + " và ".join(lacking)
    return (
        f"Thiếu {missing}: không tạo được nhãn tra cứu, cần người xem lại. "
        "Chỉ cùng nơi bán, số tiền và ngày chưa đủ để gọi là trùng."
    )


def explain_duplicate(expense: Expense, matches: list[Match]) -> str:
    """Lý do đánh dấu ứng viên trùng, ví dụ "Trùng mã biên nhận với E01; số tiền chênh +100.000 VND"."""
    parts = []
    for match in matches:
        labels = " và ".join(KEY_LABELS[k] for k in match.matched_by)
        text = f"trùng {labels} với {match.submission_id}"
        amount = next((d for d in match.differences if d.field == "amount_minor" and d.note), None)
        if amount:
            text += f" (số tiền {amount.note})"
        parts.append(text)
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + ". Người duyệt cần xác nhận hoặc bỏ nghi vấn."


def from_extraction(data) -> Expense:
    """Tạo Expense từ bản đã kiểm tra (ExpenseExtraction hoặc object có cùng tên ô)."""
    return Expense(
        submission_id=data.submission_id,
        vendor=data.vendor,
        invoice_number=data.invoice_number,
        transaction_date=data.transaction_date,
        amount_minor=data.amount_minor,
        currency=data.currency,
        receipt_id=data.receipt_id,
    )
