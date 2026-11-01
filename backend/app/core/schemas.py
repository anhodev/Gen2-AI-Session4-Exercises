"""Schema dùng chung cho cả 4 bài."""

from typing import Literal

from pydantic import BaseModel


class Issue(BaseModel):
    """Một vấn đề trong dữ liệu, hiển thị cạnh ô tương ứng trên trang web."""

    field: str
    message: str
    level: Literal["error", "warning"]  # error: chặn xác nhận; warning: chỉ nhắc
