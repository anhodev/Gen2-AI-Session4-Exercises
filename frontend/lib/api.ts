// Gọi FastAPI. Cách viết lấy từ buổi 4: một hàm request() dùng chung, đọc lỗi từ "detail".
import type {
  BookingOut,
  CheckOut,
  ConfirmResponse,
  DockExtractResponse,
  DockRequestExtraction,
  DockSample,
  ExpenseExtraction,
  ExpenseExtractResponse,
  ExpenseSample,
  ExtractRequest,
  ExtractResponse,
  Health,
  Issue,
  OrderOut,
  OrderSummary,
  PdfTextOut,
  Plan,
  PlanExtractResponse,
  PlanSample,
  PlanValidateOut,
  QueueItem,
  QueueResponse,
  ReviewAction,
  ReviewOut,
  Run,
  Sample,
  ScheduleOut,
  SlotOut,
  SubmissionOut,
  TicketExtraction,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";

/** Lỗi API kèm danh sách vấn đề theo từng ô (nếu backend trả về) và "detail" gốc. */
export class ApiError extends Error {
  constructor(message: string, public status: number, public issues: Issue[] = [], public detail: unknown = null) {
    super(message);
  }
}

// FastAPI trả "detail" theo 3 dạng:
//   - chuỗi:                         HTTPException(detail="...")
//   - list [{loc, msg}]:             lỗi 422 do Pydantic kiểm tra khuôn
//   - list [{field, message, level}]: lỗi 400 do code nghiệp vụ kiểm tra
function issuesFromDetail(detail: unknown): Issue[] {
  if (!Array.isArray(detail)) return [];
  return detail.map((item) => {
    if (item && typeof item === "object" && "field" in item) return item as Issue;
    const loc = Array.isArray(item?.loc) ? item.loc : [];
    // Bỏ phần tiền tố không cần cho người dùng: ["body", "ticket", "severity"] -> "severity"
    const field = loc.filter((p: unknown) => !["body", "ticket", "request", "expense", "plan", "query"].includes(String(p))).join(".");
    return { field: field || "(toàn bộ)", message: String(item?.msg ?? "Dữ liệu không hợp lệ"), level: "error" };
  });
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      cache: "no-store",
      // Gửi file (FormData) thì để trình duyệt tự đặt Content-Type kèm boundary
      headers: options?.body instanceof FormData ? options?.headers : { "Content-Type": "application/json", ...options?.headers },
    });
  } catch {
    throw new ApiError("Không kết nối được tới API.", 0);
  }

  if (!response.ok) {
    let message = `API trả về lỗi ${response.status}`;
    let issues: Issue[] = [];
    let detail: unknown = null;
    try {
      const body = await response.json();
      detail = body?.detail ?? null;
      if (typeof body?.detail === "string") message = body.detail;
      // Bài 02 trả 409 dạng {message, suggestion}
      if (typeof body?.detail?.message === "string") message = body.detail.message;
      issues = issuesFromDetail(body?.detail);
      if (issues.length) message = "Dữ liệu chưa hợp lệ, xem lỗi ở từng ô.";
    } catch {}
    throw new ApiError(message, response.status, issues, detail);
  }
  return response.json();
}

export const api = {
  health: () => request<Health>("/health"),
  samples: () => request<Sample[]>("/tickets/samples"),
  extract: (payload: ExtractRequest) =>
    request<ExtractResponse>("/tickets/extract", { method: "POST", body: JSON.stringify(payload) }),
  confirm: (runId: number, ticket: TicketExtraction) =>
    request<ConfirmResponse>("/tickets/confirm", { method: "POST", body: JSON.stringify({ run_id: runId, ticket }) }),
  queue: (limit: number) => request<QueueResponse>(`/tickets/queue?limit=${limit}`),
  next: () => request<QueueItem>("/tickets/next", { method: "POST" }),

  // Bài 02
  dockSamples: () => request<DockSample[]>("/docks/samples"),
  dockExtract: (email: string, receivedAt: string | null) =>
    request<DockExtractResponse>("/docks/requests/extract", {
      method: "POST", body: JSON.stringify({ email, received_at: receivedAt || null }),
    }),
  dockSuggest: (runId: number, req: DockRequestExtraction) =>
    request<SlotOut>("/docks/slots/suggest", { method: "POST", body: JSON.stringify({ run_id: runId, request: req }) }),
  dockConfirm: (runId: number, req: DockRequestExtraction, dockId: string, start: string) =>
    request<BookingOut>("/docks/bookings/confirm", {
      method: "POST", body: JSON.stringify({ run_id: runId, request: req, dock_id: dockId, start }),
    }),
  dockSchedule: (date: string) => request<ScheduleOut>(`/docks/schedule?date=${date}`),

  // Bài 03
  expenseSamples: () => request<ExpenseSample[]>("/expenses/samples"),
  expensePdfText: (file: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<PdfTextOut>("/expenses/pdf-text", { method: "POST", body: form });
  },
  expenseSamplePdf: async () => {
    const response = await fetch(`${API_URL}/expenses/sample-pdf`, { cache: "no-store" });
    if (!response.ok) throw new ApiError("Không tải được PDF mẫu", response.status);
    return response.blob();
  },
  expenseExtract: (text: string) =>
    request<ExpenseExtractResponse>("/expenses/extract", { method: "POST", body: JSON.stringify({ text }) }),
  expenseCheck: (runId: number, expense: ExpenseExtraction) =>
    request<CheckOut>("/expenses/check", { method: "POST", body: JSON.stringify({ run_id: runId, expense }) }),
  expenseReview: (submissionId: string, action: ReviewAction, expense: ExpenseExtraction | null = null, note = "") =>
    request<ReviewOut>("/expenses/review", {
      method: "POST",
      body: JSON.stringify({ submission_id: submissionId, action, expense, note: note.trim() || null }),
    }),
  expenseSubmissions: (limit = 20) => request<SubmissionOut[]>(`/expenses/submissions?limit=${limit}`),

  // Bài 04
  planSamples: () => request<PlanSample[]>("/plans/samples"),
  planExtract: (text: string) =>
    request<PlanExtractResponse>("/plans/extract", { method: "POST", body: JSON.stringify({ text }) }),
  planValidate: (runId: number, plan: Plan) =>
    request<PlanValidateOut>("/plans/validate", { method: "POST", body: JSON.stringify({ run_id: runId, plan }) }),
  planOrder: (runId: number, plan: Plan) =>
    request<OrderOut>("/plans/order", { method: "POST", body: JSON.stringify({ run_id: runId, plan }) }),
  planOrders: (runId: number, limit = 20) => request<OrderSummary[]>(`/plans/orders?run_id=${runId}&limit=${limit}`),
  planOrderDetail: (orderId: number) => request<OrderOut>(`/plans/orders/${orderId}`),

  runs: (module: string, limit = 30) => request<Run[]>(`/runs?module=${module}&limit=${limit}`),
};
