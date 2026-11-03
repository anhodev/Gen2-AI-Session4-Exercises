# Structured Output × Thuật toán

Bài tập Gen2-AI: 4 tình huống doanh nghiệp ghép thành **một ứng dụng**. Ở mỗi bài:

- **GenAI đọc và điền dữ liệu**: đọc văn bản tự do, điền vào mẫu JSON có ô rõ ràng (structured output).
- **Code kiểm tra và tính kết quả**: kiểm tra JSON, rồi dùng thuật toán để ra quyết định.

| Bài | Tình huống | Thuật toán | Trạng thái |
|---|---|---|---|
| [01](docs/bai-01-tickets/README.md) | Khách hàng nào cần được hỗ trợ trước? | Hàng đợi ưu tiên (min-heap) | ✅ Xong |
| [02](docs/bai-02-docks/README.md) | Xe giao hàng nên vào cửa kho lúc mấy giờ? | Gộp khoảng thời gian (interval) | ✅ Xong |
| 03 | Hóa đơn này đã được gửi trước đó chưa? | Tra cứu bằng hash map | Chưa làm |
| 04 | Việc nào phải làm trước? | Đồ thị phụ thuộc (topological sort) | Chưa làm |

## Công nghệ

| Phần | Dùng |
|---|---|
| Backend | FastAPI, Pydantic, SQLAlchemy, LangChain `with_structured_output` (OpenAI) |
| Database | Supabase PostgreSQL (SQLite khi chạy test hoặc chưa cấu hình) |
| Frontend | Next.js 16, React 19, Tailwind (khung giao diện lấy từ project buổi 4) |
| Test | pytest + FastAPI `TestClient` (không gọi GenAI, không cần mạng) |

## Cấu trúc thư mục

```
├── backend/
│   ├── app/
│   │   ├── main.py              # create_app + lifespan (dựng lại hàng đợi, seed cửa kho khi khởi động)
│   │   ├── core/                # dùng chung 4 bài: config, database, gọi GenAI, lịch sử xử lý
│   │   └── modules/
│   │       ├── tickets/         # Bài 01: schemas, validation, extractor, algo, store, router
│   │       └── docks/           # Bài 02: cùng cấu trúc, thêm entities (bảng cửa kho, lịch đặt)
│   ├── samples/                 # tickets.json, docks.json: dữ liệu mẫu cho chế độ mock (không tốn tiền gọi model)
│   ├── supabase/schema.sql      # tạo bảng trên Supabase
│   └── tests/
├── frontend/
│   ├── app/                     # trang chính + proxy /api -> FastAPI
│   ├── components/              # TicketWorkspace, DockWorkspace, RunHistory, ui
│   └── lib/                     # api.ts, types.ts
└── docs/                        # tài liệu, JSON Schema, ảnh chụp từng bài
```

## Cài đặt và chạy

Cần có Python 3.12 và Node.js 20 trở lên.

### 1. Backend

```bash
cd backend
python3.12 -m venv myenv
myenv/bin/pip install -r requirements.txt
cp .env.example .env
```

Các giá trị trong `backend/.env`:

| Biến | Ý nghĩa |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Key và model OpenAI. Để trống key thì app tự dùng chế độ mock |
| `MOCK_LLM` | `true`: đọc kết quả từ `samples/`, không gọi model. `false`: gọi model thật |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_DATABASE`, `DB_SSLMODE` | Kết nối Supabase. Để trống `DB_HOST` thì dùng SQLite tại `backend/data/app.db` |
| `APP_TIMEZONE` | Múi giờ hiển thị kết quả và múi giờ của kho, mặc định `Asia/Ho_Chi_Minh` (GMT+7) |
| `DOCK_OPEN`, `DOCK_CLOSE` | Giờ mở và đóng cửa kho (Bài 02), mặc định `08:00`, `18:00` |
| `FRONTEND_ORIGIN` | Địa chỉ frontend được phép gọi API (CORS) |

**Supabase** (tùy chọn):
1. Mở SQL Editor và chạy file `backend/supabase/schema.sql`.
2. Lấy thông tin kết nối ở *Project Settings → Database → Connection parameters*.

Chạy server (chỉ 1 worker, vì hàng đợi Bài 01 nằm trong bộ nhớ):

```bash
myenv/bin/uvicorn app.main:app --reload --reload-dir app --port 8000
```

Swagger UI: http://localhost:8000/docs. Gọi `GET /health` để biết đang dùng mock hay model thật, Supabase hay SQLite.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở http://localhost:3000. Trình duyệt gọi `/api/...`, rồi Next.js chuyển tiếp sang FastAPI ở `BACKEND_INTERNAL_URL` (mặc định `http://localhost:8000`). API key không bao giờ ra tới trình duyệt.

### 3. Test

```bash
cd backend
myenv/bin/python -m pytest -q
```

Hiện có 260 test, chạy trong khoảng 1 giây. Test không gọi GenAI và không cần Supabase (dùng mock và SQLite tạm).

## Dữ liệu

Mọi tin nhắn, chứng từ và tên công ty trong `samples/` đều là dữ liệu giả để học. Không gửi chứng từ hay thông tin cá nhân thật lên dịch vụ AI.
