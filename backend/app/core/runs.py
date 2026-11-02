"""Ghi lịch sử xử lý (bảng runs), dùng chung cho cả 4 bài."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .entities import Run, now_utc
from .llm import LLMResult


def create_run(db: Session, module: str, raw_input: dict, llm_result: LLMResult, issues: list[dict]) -> Run:
    """
    Lưu lại một lần GenAI đọc dữ liệu.

    Args:
        db (Session): Database session.
        module (str): Tên bài, ví dụ "tickets".
        raw_input (dict): Nội dung gốc người dùng gửi.
        llm_result (LLMResult): Kết quả gốc của GenAI.
        issues (list[dict]): Lỗi/cảnh báo sau khi code kiểm tra.

    Returns:
        Run: Bản ghi vừa tạo, có id để bước xác nhận tham chiếu tới.
    """
    run = Run(
        module=module,
        status="extracted",
        raw_input=raw_input,
        llm_output=llm_result.raw,
        issues=issues,
        error=llm_result.error,
        model=llm_result.model,
        latency_ms=llm_result.latency_ms,
        created_at=now_utc(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def changed_fields(before: dict | None, after: dict) -> list[str]:
    """
    So sánh JSON GenAI trả và bản người dùng sửa.

    Args:
        before (dict | None): JSON GenAI trả về.
        after (dict): Bản người dùng đã sửa.

    Returns:
        list[str]: Tên các ô người dùng đã sửa.
    """
    before = before or {}
    return sorted(key for key in after if before.get(key) != after[key])


def mark_confirmed(run: Run, user_output: dict, result: dict, baseline: dict | None = None) -> None:
    """
    Ghi bản người dùng sửa và kết quả cuối vào run. Người gọi tự commit.

    Args:
        run (Run): Lần extract.
        user_output (dict): Bản người dùng đã sửa.
        result (dict): Kết quả cuối.
        baseline (dict | None, optional): Bản dùng để so sánh tìm ô đã sửa. Bài 02 truyền bản
            GenAI đã chuẩn hóa giờ, để "02:00Z" và "09:00+07:00" không bị tính là đã sửa.
            Defaults to run.llm_output.
    """
    run.status = "confirmed"
    run.user_output = user_output
    before = baseline if baseline is not None else run.llm_output
    run.result = {**result, "edited_fields": changed_fields(before, user_output)}
    run.confirmed_at = now_utc()


def mark_rejected(run: Run, user_output: dict, reason: str) -> None:
    """Ghi lý do xác nhận bị từ chối vào run. Người gọi tự commit."""
    run.status = "rejected"
    run.user_output = user_output
    run.error = reason
    run.confirmed_at = now_utc()
