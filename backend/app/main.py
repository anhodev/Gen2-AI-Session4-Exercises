"""FastAPI app: create_app + lifespan + app.state.

Chạy:  myenv/bin/uvicorn app.main:app --reload --port 8000
Swagger UI: http://localhost:8000/docs
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.database import create_tables, make_session_factory
from app.core.router import router as core_router
from app.modules.docks.router import router as docks_router
from app.modules.docks.store import seed_docks
from app.modules.tickets.router import router as tickets_router
from app.modules.tickets.store import load_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi động: tạo bảng còn thiếu
    create_tables(app.state.engine)

    tz = ZoneInfo(app.state.settings.app_timezone)
    with app.state.session_factory() as db:
        # Bài 01: dựng lại hàng đợi ticket từ database
        app.state.ticket_queue = load_queue(db, tz)
        # Bài 02: tạo cửa A, B, C và lịch có sẵn nếu database còn trống
        seed_docks(db, tz)

    # Khóa để hai request không cùng sửa heap một lúc
    app.state.ticket_lock = threading.Lock()
    # Khóa để hai người không cùng đặt một giờ (thêm cho SQLite, vì SQLite bỏ qua FOR UPDATE)
    app.state.dock_lock = threading.Lock()

    yield  # app chạy trong lúc này

    # Tắt app: đóng kết nối database
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Tạo FastAPI app.

    Args:
        settings (Settings | None, optional): Cấu hình. Test truyền vào để dùng
            SQLite tạm và chế độ mock. Defaults to get_settings().

    Returns:
        FastAPI: App đã gắn database, CORS và các router.
    """
    settings = settings or get_settings()
    app = FastAPI(title="Structured Output × Thuật toán", lifespan=lifespan)

    # Lưu engine, session factory và cấu hình vào app.state để endpoint dùng chung
    engine, session_factory = make_session_factory(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.settings = settings

    # Cho phép frontend (Next.js) gọi API từ trình duyệt
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(core_router)
    app.include_router(tickets_router)
    app.include_router(docks_router)
    return app


app = create_app()
