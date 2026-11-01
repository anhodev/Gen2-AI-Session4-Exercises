-- Chạy file này trong Supabase SQL Editor (chạy lại nhiều lần vẫn an toàn).
-- Khớp với app/core/entities.py và app/modules/tickets/entities.py.
-- Backend cũng tự tạo bảng còn thiếu khi khởi động, nhưng không bật RLS như file này.

-- Lịch sử mỗi lần xử lý, dùng chung cho cả 4 bài
create table if not exists public.runs (
  id bigserial primary key,
  module varchar(20) not null,                 -- tickets, docks, expenses, plans
  status varchar(20) not null default 'extracted'
    check (status in ('extracted', 'confirmed', 'rejected')),
  raw_input jsonb not null,                    -- nội dung gốc người dùng gửi
  llm_output jsonb,                            -- JSON GenAI trả về
  user_output jsonb,                           -- bản người dùng đã sửa
  result jsonb,                                -- kết quả cuối của thuật toán
  issues jsonb,                                -- lỗi/cảnh báo lúc kiểm tra
  error text,                                  -- lỗi gọi model hoặc lý do từ chối
  model varchar(50) not null,                  -- tên model, hoặc 'mock'
  latency_ms integer not null default 0 check (latency_ms >= 0),
  created_at timestamptz not null default now(),
  confirmed_at timestamptz
);

create index if not exists ix_runs_module on public.runs (module);

-- Bài 01: ticket đã được nhân viên xác nhận
create table if not exists public.tickets (
  ticket_id varchar(50) primary key,           -- mã hệ thống cấp, khóa chính chặn mã trùng
  run_id bigint references public.runs(id) on delete set null,
  intent varchar(20) not null check (intent in ('billing', 'technical', 'account', 'other')),
  severity varchar(20) not null check (severity in ('low', 'medium', 'high', 'critical')),
  product varchar(100),
  summary varchar(200) not null,
  needs_human_review boolean not null default false,
  received_at timestamptz not null,
  deadline_at timestamptz not null,            -- code tính từ received_at + SLA
  status varchar(20) not null default 'queued' check (status in ('queued', 'done')),
  created_at timestamptz not null default now(),
  popped_at timestamptz
);

create index if not exists ix_tickets_status on public.tickets (status);

-- Bật Row Level Security: chặn truy cập trực tiếp qua Supabase API bằng anon key.
-- Backend kết nối bằng user postgres nên không bị ảnh hưởng.
alter table public.runs enable row level security;
alter table public.tickets enable row level security;
