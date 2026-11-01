"""Cấu hình app, đọc từ file .env (API key chỉ nằm ở backend)."""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Đọc .env vào biến môi trường. Biến đã có sẵn trong shell sẽ không bị ghi đè.
load_dotenv()

SQLITE_FALLBACK = "sqlite:///./data/app.db"


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    mock_llm: bool = True
    database_url: str = SQLITE_FALLBACK
    frontend_origin: str = "http://localhost:3000"
    app_timezone: str = "Asia/Ho_Chi_Minh"  # múi giờ của app (GMT+7), dùng khi email không ghi múi giờ
    dock_open: str = "08:00"   # Bài 02: giờ mở cửa kho
    dock_close: str = "18:00"  # Bài 02: giờ đóng cửa kho

    @property
    def use_mock(self) -> bool:
        # Chưa có key thì luôn dùng mock, tránh app lỗi khi chạy thử.
        return self.mock_llm or not self.openai_api_key


def build_database_url() -> str:
    """
    Ghép URL kết nối Supabase PostgreSQL từ các biến DB_* trong .env.

    Lấy thông tin ở Supabase: Project Settings > Database > Connection parameters.

    Returns:
        str: postgresql+psycopg://... nếu đủ biến, ngược lại dùng SQLite local.
    """
    db_host = os.getenv("DB_HOST", "").strip()
    db_port = os.getenv("DB_PORT", "5432").strip()
    db_user = os.getenv("DB_USER", "").strip()
    db_password = os.getenv("DB_PASSWORD", "")
    db_database = os.getenv("DB_DATABASE", "postgres").strip()
    db_sslmode = os.getenv("DB_SSLMODE", "require").strip()

    if not (db_host and db_user and db_password):
        return SQLITE_FALLBACK

    # quote_plus: mật khẩu có ký tự đặc biệt như @ / # vẫn ghép đúng vào URL
    return (
        f"postgresql+psycopg://{quote_plus(db_user)}:{quote_plus(db_password)}"
        f"@{db_host}:{db_port}/{db_database}?sslmode={quote_plus(db_sslmode)}"
    )


@lru_cache
def get_settings() -> Settings:
    """Đọc cấu hình một lần, các lần sau dùng lại kết quả."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        mock_llm=os.getenv("MOCK_LLM", "true").strip().lower() == "true",
        database_url=build_database_url(),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        app_timezone=os.getenv("APP_TIMEZONE", "Asia/Ho_Chi_Minh"),
        dock_open=os.getenv("DOCK_OPEN", "08:00"),
        dock_close=os.getenv("DOCK_CLOSE", "18:00"),
    )
