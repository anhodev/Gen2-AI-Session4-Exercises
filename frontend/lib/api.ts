// Gọi FastAPI. Cách viết lấy từ buổi 4: một hàm request() dùng chung, đọc lỗi từ "detail".
import type {
  ConfirmResponse,
  ExtractRequest,
  ExtractResponse,
  Health,
  Issue,
  QueueItem,
  QueueResponse,
  Run,
  Sample,
  TicketExtraction,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";

/** Lỗi API kèm danh sách vấn đề theo từng ô (nếu backend trả về). */
export class ApiError extends Error {
  constructor(message: string, public status: number, public issues: Issue[] = []) {
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
    const field = loc.filter((p: unknown) => !["body", "ticket", "query"].includes(String(p))).join(".");
    return { field: field || "(toàn bộ)", message: String(item?.msg ?? "Dữ liệu không hợp lệ"), level: "error" };
  });
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
  } catch {
    throw new ApiError("Không kết nối được tới API.", 0);
  }

  if (!response.ok) {
    let message = `API trả về lỗi ${response.status}`;
    let issues: Issue[] = [];
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
      issues = issuesFromDetail(body?.detail);
      if (issues.length) message = "Dữ liệu chưa hợp lệ, xem lỗi ở từng ô.";
    } catch {}
    throw new ApiError(message, response.status, issues);
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
  runs: (module: string, limit = 30) => request<Run[]>(`/runs?module=${module}&limit=${limit}`),
};
