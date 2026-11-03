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
  checks: DockCheck[];
}

export interface BookingOut {
  booking_id: number;
  request_id: string;
  dock_id: string;
  carrier: string | null;
  start: string;
  end: string;
  edited_fields: string[];
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
  docks: { dock_id: string; dock_type: DockType; bookings: ScheduleBooking[] }[];
}
