"""Test ghép URL kết nối Supabase từ biến môi trường. Không kết nối thật."""

from app.core.config import SQLITE_FALLBACK, build_database_url

DB_KEYS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_DATABASE", "DB_SSLMODE")


def clear_db_env(monkeypatch):
    for key in DB_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_supabase_url_from_env(monkeypatch):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "aws-0-ap-southeast-1.pooler.supabase.com")
    monkeypatch.setenv("DB_USER", "postgres.abcxyz")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    assert build_database_url() == (
        "postgresql+psycopg://postgres.abcxyz:secret"
        "@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )


def test_password_with_special_characters_is_encoded(monkeypatch):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_USER", "postgres")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/w#rd")
    assert ":p%40ss%2Fw%23rd@db.example.com" in build_database_url()


def test_missing_db_env_falls_back_to_sqlite(monkeypatch):
    clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "db.example.com")  # thiếu user/password
    assert build_database_url() == SQLITE_FALLBACK
