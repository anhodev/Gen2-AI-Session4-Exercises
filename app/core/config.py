"""Cấu hình app, đọc từ file .env (API key chỉ nằm ở backend)."""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

# Đọc .env vào biến môi trường. Biến đã có sẵn trong shell sẽ không bị ghi đè.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    mock_llm: bool = True
    database_url: str = "sqlite:///./data/app.db"

    @property
    def use_mock(self) -> bool:
        # Chưa có key thì luôn dùng mock, tránh app lỗi khi chạy thử.
        return self.mock_llm or not self.openai_api_key


@lru_cache
def get_settings() -> Settings:
    """Đọc cấu hình một lần, các lần sau dùng lại kết quả."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        mock_llm=os.getenv("MOCK_LLM", "true").strip().lower() == "true",
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/app.db"),
    )
