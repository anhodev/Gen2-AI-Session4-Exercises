"""Bài 03 - Lấy chữ từ PDF có sẵn lớp chữ (PDF xuất từ phần mềm, hoặc đã OCR).

PDF chỉ chứa ảnh scan thì không có chữ để lấy: app báo rõ để người dùng OCR trước
rồi dán văn bản, chứ không đoán. Ảnh chụp (JPG, PNG) chưa hỗ trợ.
"""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .algo import ExpenseError

MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_PAGES = 10                   # chứng từ chi phí thường chỉ 1-2 trang
MAX_TEXT = 20000                 # bằng giới hạn của ExtractRequest.text
MIN_ALNUM = 10                   # ít hơn số chữ/số này coi như không có lớp chữ


class PdfTextError(ExpenseError):
    """Không lấy được chữ từ PDF."""


def pdf_to_text(data: bytes) -> str:
    """
    Lấy chữ từ PDF, mỗi trang cách nhau một dòng trống.

    Args:
        data (bytes): Nội dung file PDF.

    Raises:
        PdfTextError: Không phải PDF, PDF hỏng, có mật khẩu, quá lớn,
            quá nhiều trang, hoặc không có lớp chữ (ảnh scan).

    Returns:
        str: Văn bản trong PDF, đã bỏ khoảng trắng thừa ở đầu/cuối mỗi dòng.
    """
    if len(data) > MAX_PDF_BYTES:
        raise PdfTextError(f"File PDF lớn hơn {MAX_PDF_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF"):
        raise PdfTextError("File không phải PDF")

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfTextError("PDF có mật khẩu, hãy bỏ mật khẩu rồi tải lại")
        if len(reader.pages) > MAX_PAGES:
            raise PdfTextError(f"PDF có {len(reader.pages)} trang, tối đa {MAX_PAGES} trang cho một chứng từ")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfTextError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        # File hỏng có thể làm pypdf ném nhiều loại lỗi khác nhau
        raise PdfTextError(f"Không đọc được PDF (file hỏng hoặc sai định dạng): {exc}")

    text = "\n\n".join(clean(page) for page in pages if page.strip())
    if sum(ch.isalnum() for ch in text) < MIN_ALNUM:
        raise PdfTextError("PDF không có lớp chữ (có thể là ảnh scan). Hãy OCR trước rồi dán văn bản vào ô nhập.")
    if len(text) > MAX_TEXT:
        raise PdfTextError(f"Văn bản trong PDF dài {len(text)} ký tự, tối đa {MAX_TEXT}")
    return text


def clean(page_text: str) -> str:
    """Bỏ khoảng trắng thừa ở đầu/cuối mỗi dòng và các dòng trống."""
    return "\n".join(line.strip() for line in page_text.splitlines() if line.strip())
