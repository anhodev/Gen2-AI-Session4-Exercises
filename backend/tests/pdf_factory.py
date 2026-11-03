"""Tạo PDF nhỏ có lớp chữ để test, không cần thư viện tạo PDF.

Dùng font chuẩn Helvetica nên chỉ viết được chữ không dấu (ASCII).
Chạy trực tiếp để tạo lại file mẫu: myenv/bin/python -m tests.pdf_factory
"""

from __future__ import annotations

from pathlib import Path


def escape(text: str) -> str:
    """Thoát các ký tự đặc biệt trong chuỗi PDF."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """
    Tạo PDF, mỗi trang là một danh sách dòng chữ. Trang rỗng giống trang ảnh scan (không có chữ).

    Args:
        pages (list[list[str]]): Các trang, mỗi trang gồm các dòng.

    Returns:
        bytes: Nội dung file PDF.
    """
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for pid, lines in zip(page_ids, pages):
        body = "BT /F1 12 Tf 14 TL 50 800 Td " + " ".join(f"({escape(line)}) Tj T*" for line in lines) + " ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {pid + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# Chứng từ mẫu dạng PDF, cùng nội dung với "Mẫu 7" trong samples/expenses.json
SAMPLE_PDF = Path(__file__).resolve().parents[1] / "samples" / "expenses" / "sunrise-hotel.pdf"
SAMPLE_LINES = [
    "SUNRISE HOTEL SINGAPORE",
    "Invoice No: SH-2026-118",
    "Receipt: RC-7781",
    "Date: 2026-10-18",
    "Total: USD 125.50",
]

if __name__ == "__main__":
    SAMPLE_PDF.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PDF.write_bytes(make_pdf([SAMPLE_LINES]))
    print(f"Đã tạo {SAMPLE_PDF}")
