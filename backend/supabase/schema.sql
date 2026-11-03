-- Chạy file này trong Supabase SQL Editor (chạy lại nhiều lần vẫn an toàn).
-- Khớp với app/core/entities.py và app/modules/*/entities.py.
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

-- Bài 01: mã ticket do backend cấp (T01, T02...)
create table if not exists public.ticket_numbers (
  id bigserial primary key,                    -- số tự tăng, backend sinh mã T01, T02...
  ticket_id varchar(50) unique,
  run_id bigint references public.runs(id) on delete set null,
  created_at timestamptz not null default now()
);

-- Bài 02: cửa kho, mã yêu cầu, lịch đặt
-- btree_gist: cần để ràng buộc EXCLUDE so sánh "=" trên cột dock_id
create extension if not exists btree_gist;

create table if not exists public.docks (
  dock_id varchar(20) primary key,
  dock_type varchar(20) not null,
  constraint ck_docks_type check (dock_type in ('standard', 'refrigerated'))
);

create table if not exists public.dock_requests (
  id bigserial primary key,                    -- số tự tăng, backend sinh mã R001, R002...
  request_id varchar(20) unique,
  run_id bigint references public.runs(id) on delete set null,
  status varchar(20) not null default 'new' check (status in ('new', 'booked')),
  created_at timestamptz not null default now()
);

create table if not exists public.dock_bookings (
  id bigserial primary key,
  dock_id varchar(20) not null references public.docks(dock_id),
  request_id varchar(20) unique,               -- null với lịch có sẵn; mỗi yêu cầu chỉ đặt 1 lần
  run_id bigint references public.runs(id) on delete set null,
  carrier varchar(100),
  start_at timestamptz not null,
  end_at timestamptz not null,
  created_at timestamptz not null default now(),
  constraint ck_dock_bookings_end_after_start check (end_at > start_at),
  -- Database tự từ chối 2 lịch chồng nhau trên cùng một cửa.
  -- '[)': lịch kết thúc 10:00 và lịch bắt đầu 10:00 KHÔNG bị coi là chồng nhau.
  constraint ex_dock_bookings_no_overlap exclude using gist (
    dock_id with =,
    tstzrange(start_at, end_at, '[)') with &&
  )
);

create index if not exists ix_dock_bookings_dock_id on public.dock_bookings (dock_id);

-- Bài 03: chứng từ chi phí và nhật ký duyệt
create table if not exists public.expense_submissions (
  id bigserial primary key,                    -- số tự tăng, backend sinh mã E01, E02...
  submission_id varchar(20) unique,
  run_id bigint references public.runs(id) on delete set null,
  vendor varchar(200),
  invoice_number varchar(64),
  transaction_date date,
  amount_minor bigint check (amount_minor >= 0),  -- đơn vị nhỏ nhất (đồng, cent), số nguyên
  currency varchar(3) check (currency in ('VND', 'USD')),
  receipt_id varchar(64),
  status varchar(20) not null default 'new' check (status in ('new', 'checked')),
  check_status varchar(30)
    check (check_status in ('duplicate_candidate', 'needs_review', 'unique_under_current_rules')),
  matched_ids jsonb,                           -- mã các chứng từ cũ trùng nhãn
  review_status varchar(30)
    check (review_status in ('pending', 'confirmed_duplicate', 'dismissed')),
  created_at timestamptz not null default now(),
  checked_at timestamptz,
  reviewed_at timestamptz
);

create index if not exists ix_expense_submissions_status on public.expense_submissions (status);

create table if not exists public.expense_reviews (
  id bigserial primary key,
  submission_id varchar(20) not null,
  action varchar(30) not null check (action in ('confirm_duplicate', 'dismiss', 'recheck')),
  before jsonb,                                -- thông tin chứng từ trước khi sửa
  after jsonb,                                 -- sau khi sửa
  result jsonb,                                -- kết quả kiểm tra lại
  note text,
  created_at timestamptz not null default now()
);

create index if not exists ix_expense_reviews_submission_id on public.expense_reviews (submission_id);

-- Bài 04: mỗi lần tính thứ tự công việc
create table if not exists public.plan_orders (
  id bigserial primary key,
  run_id bigint not null references public.runs(id) on delete cascade,  -- lần extract biên bản
  version integer not null check (version >= 1),  -- lần tính thứ mấy của biên bản này
  tasks jsonb not null,                        -- [{id, title, owner}] mã T01…
  dependencies jsonb not null,                 -- [{before, after, evidence}] đã bỏ dòng ghi lặp
  status varchar(20) not null check (status in ('ordered', 'cycle_detected')),
  result jsonb not null,                       -- thứ tự kèm lý do, hoặc vòng và các việc bị kẹt
  warnings jsonb not null,                     -- cảnh báo lúc tính (không chặn)
  created_at timestamptz not null default now()
);

create index if not exists ix_plan_orders_run_id on public.plan_orders (run_id);
create index if not exists ix_plan_orders_status on public.plan_orders (status);

-- Bật Row Level Security: chặn truy cập trực tiếp qua Supabase API bằng anon key.
-- Backend kết nối bằng user postgres nên không bị ảnh hưởng.
alter table public.runs enable row level security;
alter table public.tickets enable row level security;
alter table public.ticket_numbers enable row level security;
alter table public.docks enable row level security;
alter table public.dock_requests enable row level security;
alter table public.dock_bookings enable row level security;
alter table public.expense_submissions enable row level security;
alter table public.expense_reviews enable row level security;
alter table public.plan_orders enable row level security;
