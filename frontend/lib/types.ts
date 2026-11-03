// Kiểu dữ liệu khớp với schema Pydantic ở backend/app/modules/tickets/schemas.py

export type Severity = "low" | "medium" | "high" | "critical";
export type Intent = "billing" | "technical" | "account" | "other";

export interface Issue {
  field: string;
  message: string;
  level: "error" | "warning";
}

export interface TicketExtraction {
  ticket_id: string;
  intent: Intent;
  severity: Severity;
  product: string | null;
  summary: string;
  needs_human_review: boolean;
}

export interface ExtractRequest {
  // Không có ticket_id: backend tự cấp
  received_at: string; // ISO 8601, bắt buộc có múi giờ, ví dụ 2026-10-21T09:00:00+07:00
  message: string;
}

export interface ExtractResponse {
  run_id: number | null;
  ticket_id: string; // mã backend vừa cấp
  received_at: string;
  llm_output: Record<string, unknown> | null; // JSON GenAI trả về, có thể sai khuôn
  issues: Issue[];
  can_confirm: boolean;
}

export interface ConfirmResponse {
  ticket_id: string;
  severity: Severity;
  received_at: string;
  deadline_at: string;
  sla_minutes: number;
  edited_fields: string[];
  warnings: Issue[];
}

export interface QueueItem {
  position: number;
  ticket_id: string;
  severity: Severity;
  intent: Intent;
  product: string | null;
  summary: string;
  received_at: string;
  deadline_at: string;
  reason: string;
}

export interface QueueResponse {
  total: number;
  items: QueueItem[];
}

export interface Sample {
  label: string;
  note: string;
  received_at: string;
  message: string;
}

export interface Health {
  status: string;
  llm: string;       // "mock" hoặc tên model
  database: string;  // "postgresql" hoặc "sqlite"
}

export interface Run {
  id: number;
  module: string;
  status: "extracted" | "confirmed" | "rejected";
  raw_input: Record<string, unknown>;
  llm_output: Record<string, unknown> | null;
  user_output: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  issues: Issue[] | null;
  error: string | null;
  model: string;
  latency_ms: number;
  created_at: string;
  confirmed_at: string | null;
}

// --- Bài 02: khớp với backend/app/modules/docks/schemas.py ---

export type DockType = "standard" | "refrigerated";
export type DockMissingField = "carrier" | "earliest_start" | "duration_minutes";

export interface DockRequestExtraction {
  request_id: string;
  carrier: string | null;
  earliest_start: string | null; // ISO; gửi lên không kèm múi giờ thì backend hiểu là giờ kho (GMT+7)
  duration_minutes: number | null;
  dock_type: DockType;
  missing_fields: DockMissingField[];
}

export interface DockSample {
  label: string;
  note: string;
  received_at: string;
  email: string;
}

export interface DockExtractResponse {
  run_id: number | null;
  request_id: string;
  llm_output: Record<string, unknown> | null;  // JSON GenAI trả về, giữ nguyên
  extraction: DockRequestExtraction | null;    // bản đã kiểm tra, giờ đã ở múi giờ kho
  issues: Issue[];
  can_suggest: boolean;
}

export interface BusyBlock {
  start: string;
  end: string;
}

export interface DockCheck {
  dock_id: string;
  busy: BusyBlock[];
  free_from: string | null;
}

export interface SlotOut {
  status: "ok" | "no_slot";
  dock_id: string | null;
  start: string | null;
  end: string | null;
  reason: string;
  checks: DockCheck[]; // khoảng bận đã gồm thời gian dọn cửa
  cleanup_minutes: number;
}

export interface BookingOut {
  booking_id: number;
  request_id: string;
  dock_id: string;
  carrier: string | null;
  start: string;
  end: string;
  edited_fields: string[];
  cleanup_minutes: number; // cửa còn bận thêm chừng này phút sau giờ kết thúc
}

export interface ScheduleBooking {
  request_id: string | null; // null: lịch có sẵn
  carrier: string | null;
  start: string;
  end: string;
}

export interface ScheduleOut {
  date: string;
  timezone: string; // ví dụ "GMT+7"
  open_at: string;
  close_at: string;
  cleanup_minutes: number; // số phút dọn cửa sau mỗi xe
  docks: { dock_id: string; dock_type: DockType; bookings: ScheduleBooking[] }[];
}

// --- Bài 03: khớp với backend/app/modules/expenses/schemas.py ---

export type ExpenseMissingField = "vendor" | "invoice_number" | "transaction_date" | "amount_minor" | "currency" | "receipt_id";
export type CheckStatus = "duplicate_candidate" | "needs_review" | "unique_under_current_rules";
export type ReviewStatus = "pending" | "confirmed_duplicate" | "dismissed";
export type ReviewAction = "confirm_duplicate" | "dismiss" | "recheck";

export interface ExpenseExtraction {
  submission_id: string;
  vendor: string | null;
  invoice_number: string | null;
  transaction_date: string | null; // YYYY-MM-DD
  amount_minor: number | null;     // đơn vị nhỏ nhất: VND là đồng, USD là cent
  currency: string | null;
  receipt_id: string | null;
  missing_fields: ExpenseMissingField[];
}

export interface ExpenseSample {
  label: string;
  note: string;
  text: string;
}

export interface ExpenseExtractResponse {
  run_id: number | null;
  submission_id: string; // mã backend vừa cấp
  llm_output: Record<string, unknown> | null;
  extraction: ExpenseExtraction | null;
  issues: Issue[];
  can_check: boolean;
}

export interface PdfTextOut {
  filename: string | null;
  text: string;
}

export interface SubmissionOut {
  submission_id: string;
  vendor: string | null;
  invoice_number: string | null;
  transaction_date: string | null;
  amount_minor: number | null;
  currency: string | null;
  receipt_id: string | null;
  amount_text: string | null; // vd "1.250.000 VND"
  check_status: CheckStatus | null;
  matched_ids: string[];
  review_status: ReviewStatus | null;
  checked_at: string | null;
  reviewed_at: string | null;
}

export interface FieldDiffOut {
  field: string;
  current: string | null;
  previous: string | null;
  note: string;
}

export interface MatchOut {
  submission_id: string;
  matched_by: ("receipt" | "invoice" | "receipt_similar" | "invoice_similar")[];
  differences: FieldDiffOut[];
  previous: SubmissionOut;
}

export interface CheckOut {
  submission_id: string;
  status: CheckStatus;
  reason: string;
  keys: string[][];
  matches: MatchOut[];
  review_status: ReviewStatus | null;
  warnings: Issue[];
}

export interface ReviewOut {
  submission: SubmissionOut;
  result: CheckOut | null;
}

// --- Bài 04: khớp với backend/app/modules/plans/schemas.py ---

export interface PlanTask {
  id: string | null; // mã backend cấp (T01…); null với việc mới thêm, chưa có mã
  title: string;
  owner: string | null;
}

export interface PlanDependency {
  before: string;
  after: string;
  evidence: string | null; // câu trong biên bản; null với quan hệ người dùng tự thêm
}

export interface Plan {
  tasks: PlanTask[];
  dependencies: PlanDependency[];
}

export interface PlanSample {
  label: string;
  note: string;
  text: string;
}

export interface PlanExtractResponse {
  run_id: number | null;
  llm_output: Record<string, unknown> | null; // JSON GenAI trả, mã tạm A, B…
  llm_issues: Issue[];                        // vấn đề của JSON gốc
  plan: Plan | null;                          // đã đổi sang mã T01…
  issues: Issue[];                            // vấn đề của plan, đúng dòng người dùng sửa
  can_order: boolean;
}

export interface PlanValidateOut {
  plan: Plan | null;
  issues: Issue[];
  can_order: boolean;
}

export type OrderStatus = "ordered" | "cycle_detected";

export interface OrderStep {
  position: number;
  task: PlanTask;
  waits_for: string[];
  reason: string;
  stage: number | null; // đợt sớm nhất làm được; null với lần tính lưu trước khi có chia đợt
}

export interface OrderStage {
  stage: number;
  tasks: PlanTask[]; // không chờ nhau, làm cùng lúc được
}

export interface OrderOut {
  order_id: number;
  run_id: number;
  version: number;
  status: OrderStatus;
  reason: string;
  steps: OrderStep[];                    // rỗng khi có vòng
  stages: OrderStage[];                  // nhóm việc làm cùng lúc; rỗng khi có vòng
  cycle: string[];                       // vd ["T01", "T03", "T04", "T01"]
  cycle_dependencies: PlanDependency[];
  blocked: PlanTask[];
  plan: Plan;
  warnings: Issue[];
  created_at: string;
}

export interface OrderSummary {
  order_id: number;
  run_id: number;
  version: number;
  status: OrderStatus;
  reason: string;
  task_count: number;
  dependency_count: number;
  created_at: string;
}
