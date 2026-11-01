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
  ticket_id: string;
  received_at: string; // ISO 8601, bắt buộc có múi giờ, ví dụ 2026-10-21T09:00:00+07:00
  message: string;
}

export interface ExtractResponse {
  run_id: number | null;
  ticket_id: string;
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
  ticket_id: string;
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
