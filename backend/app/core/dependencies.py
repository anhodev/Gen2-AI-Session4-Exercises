"""Dependency dùng chung cho các endpoint, viết theo buổi 5."""

from zoneinfo import ZoneInfo

from fastapi import Request

from .config import Settings


def get_db(request: Request):
    """
    Mở một database session cho mỗi request, tự đóng khi xong.

    Args:
        request (Request): FastAPI request, chứa session_factory trong app.state.
    """
    with request.app.state.session_factory() as db:
        yield db


def get_app_settings(request: Request) -> Settings:
    """Lấy cấu hình đã nạp lúc tạo app."""
    return request.app.state.settings


def get_display_tz(request: Request) -> ZoneInfo:
    """Múi giờ dùng để hiển thị thời gian trong kết quả trả về."""
    return ZoneInfo(request.app.state.settings.app_timezone)
