"""Bài 03 - Thuật toán tìm chứng từ có thể đã được gửi trước đó.

  - Chuẩn hóa trước khi so: Unicode NFKC, bỏ dấu cách thừa, không phân biệt hoa/thường.
      KHÔNG xóa dấu gạch nối: INV-0042 và INV0042 có thể là hai mã khác nhau.
  - Mỗi chứng từ có tối đa 2 nhãn tra cứu:
      1. loại tiền + mã biên nhận
      2. loại tiền + nơi bán + số hóa đơn (tìm được bản gửi sau bị thiếu mã biên nhận)
  - Hash map: nhãn -> các chứng từ cũ có nhãn đó. Tra một nhãn O(1) trung bình,
      n chứng từ khoảng O(n).
  - Mỗi nhãn có thêm 1 bản "gần giống": gộp các ký tự OCR hay đọc nhầm (O/0, I/l/1, Z/2, S/5, B/8, G/6).
      Hai mã khác nhau nhưng gộp ra giống nhau (INV-0O42 và INV-0042) chỉ được đưa vào
      danh sách cần xem lại, vì hai hóa đơn khác nhau có thể trông rất giống nhau.
  - Kết quả:
      duplicate_candidate         có nhãn đã gặp: cho biết chứng từ cũ và các ô khác nhau
      needs_review                không tạo được nhãn nào (thiếu mã, thiếu loại tiền),
                                  hoặc chỉ có mã gần giống chứng từ cũ
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
KEY_LABELS = {
    "receipt": "mã biên nhận",
    "invoice": "nơi bán + số hóa đơn",
    "receipt_similar": "mã biên nhận",
    "invoice_similar": "số hóa đơn",
}

# Ký tự OCR hay đọc nhầm -> ký tự đại diện (áp dụng sau khi đã chuyển chữ thường).
# Chỉ thay từng ký tự, không xóa ký tự nào: INV-0042 và INV0042 vẫn khác nhau.
OCR_CONFUSABLE = str.maketrans({"o": "0", "i": "1", "l": "1", "|": "1", "z": "2", "s": "5", "b": "8", "g": "6"})


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


def ocr_fold(value: str | None) -> str | None:
    """
    Gộp các ký tự OCR hay đọc nhầm, vd "INV-0O42" và "inv-0042" cùng thành "1nv-0042".

    Returns:
        str | None: Chuỗi đã chuẩn hóa và gộp, None nếu rỗng.
    """
    text = normalize_text(value)
    return text.translate(OCR_CONFUSABLE) if text else None


def similar_keys(expense: Expense) -> list[tuple]:
    """
    Nhãn "gần giống" của chứng từ: giống lookup_keys nhưng mã đã qua ocr_fold.
    Nơi bán vẫn phải giống hệt sau chuẩn hóa (tên không phải mã, không gộp ký tự).

    Returns:
        list[tuple]: vd ("receipt_similar", "VND", "r-42"), ("invoice_similar", "VND", "abc travel", "1nv-0042").
    """
    if expense.currency is None:
        return []
    keys = []
    receipt = ocr_fold(expense.receipt_id)
    if receipt:
        keys.append(("receipt_similar", expense.currency, receipt))
    vendor, invoice = normalize_text(expense.vendor), ocr_fold(expense.invoice_number)
    if vendor and invoice:
        keys.append(("invoice_similar", expense.currency, vendor, invoice))
    return keys


def all_keys(expense: Expense) -> list[tuple]:
    """Nhãn chính xác + nhãn gần giống, là những gì bảng tra cứu lưu."""
    return lookup_keys(expense) + similar_keys(expense)


# --- Bảng tra cứu (hash map) ------------------------------------------------

class ExpenseIndex:
    """
    Bảng tra cứu chứng từ đã gửi: nhãn -> danh sách mã chứng từ (theo thứ tự đã thêm).

    Thêm, xóa, tra một chứng từ: O(1) trung bình (mỗi chứng từ có tối đa 4 nhãn).
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
        for key in all_keys(expense):
            self._by_key[key].append(expense.submission_id)

    def remove(self, submission_id: str) -> Expense | None:
        """Bỏ chứng từ khỏi bảng, vd trước khi kiểm tra lại bản đã sửa mã biên nhận."""
        expense = self._expenses.pop(submission_id, None)
        if expense is None:
            return None
        del self._order[submission_id]
        for key in all_keys(expense):
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

        # Nhãn gần giống: chỉ tính khi mã đó chưa trùng chính xác với cùng chứng từ cũ
        for key in similar_keys(expense):
            exact = key[0].removesuffix("_similar")
            for old_id in self._by_key.get(key, []):
                if old_id != expense.submission_id and exact not in matched_by.get(old_id, []):
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
        if any(is_exact(m) for m in matches):
            return CheckResult(DUPLICATE, keys, matches, explain_duplicate(expense, matches))
        # Chỉ gần giống: không đủ để gọi là trùng
        return CheckResult(NEEDS_REVIEW, keys, matches, explain_similar(matches))

    def check_and_add(self, expense: Expense) -> CheckResult:
        """Kiểm tra rồi thêm vào bảng (dùng khi lưu chứng từ mới)."""
        result = self.check(expense)
        self.add(expense)
        return result


# --- So sánh và lý do -------------------------------------------------------

def is_exact(match: Match) -> bool:
    """Chứng từ cũ có ít nhất một nhãn trùng chính xác (không chỉ gần giống)."""
    return any(not k.endswith("_similar") for k in match.matched_by)


def ocr_pairs(a: str, b: str) -> list[str]:
    """
    Các cặp ký tự khác nhau giữa 2 mã gần giống, vd ("INV-0O42", "INV-0042") -> ["O/0"].
    Hai mã cùng độ dài sau chuẩn hóa vì ocr_fold chỉ thay từng ký tự.
    """
    na, nb = " ".join(unicodedata.normalize("NFKC", a).split()), " ".join(unicodedata.normalize("NFKC", b).split())
    pairs = []
    for x, y in zip(na, nb):
        if x.casefold() != y.casefold() and f"{x}/{y}" not in pairs:
            pairs.append(f"{x}/{y}")
    return pairs


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
        note = ""
        if a is not None and b is not None:
            if normalize_text(a) == normalize_text(b):
                note = "chỉ khác chữ hoa/dấu cách"
            elif name != "vendor" and ocr_fold(a) == ocr_fold(b):
                note = f"gần giống, có thể OCR đọc nhầm {', '.join(ocr_pairs(a, b))}"
        diffs.append(FieldDiff(name, a, b, note))

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
        exact = [KEY_LABELS[k] for k in match.matched_by if not k.endswith("_similar")]
        similar = [KEY_LABELS[k] for k in match.matched_by if k.endswith("_similar")]
        if exact:
            text = f"trùng {' và '.join(exact)} với {match.submission_id}"
            if similar:
                text += f", {' và '.join(similar)} gần giống"
        else:
            text = f"{' và '.join(similar)} gần giống {match.submission_id}"
        amount = next((d for d in match.differences if d.field == "amount_minor" and d.note), None)
        if amount:
            text += f" (số tiền {amount.note})"
        parts.append(text)
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + ". Người duyệt cần xác nhận hoặc bỏ nghi vấn."


def explain_similar(matches: list[Match]) -> str:
    """Lý do khi chỉ có mã gần giống: đưa vào danh sách cần xem lại, không gọi là trùng."""
    parts = []
    for match in matches:
        labels = " và ".join(KEY_LABELS[k] for k in match.matched_by)
        pairs = [d.note.split("đọc nhầm ")[-1] for d in match.differences if d.note.startswith("gần giống")]
        text = f"{labels} gần giống {match.submission_id}"
        if pairs:
            text += f" (có thể OCR đọc nhầm {', '.join(pairs)})"
        parts.append(text)
    text = "; ".join(parts)
    return (
        text[0].upper() + text[1:] + ". Hai hóa đơn khác nhau có thể có mã rất giống nhau, "
        "nên chưa gọi là trùng: cần người xem lại."
    )


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
