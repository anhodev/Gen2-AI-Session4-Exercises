"""Bài 01 - Thuật toán xếp hàng đợi ticket.
  - Hạn xử lý (SLA) do code tính từ received_at, GenAI không đoán.
      critical 30 phút, high 2 giờ, medium 8 giờ, low 24 giờ.
  - Khóa so sánh: (deadline_at, -severity_rank, received_at, ticket_id)
      -> hạn gần nhất lên trước; cùng hạn thì mức khẩn cấp cao hơn;
         vẫn bằng thì tin đến trước; vẫn bằng thì mã ticket theo alphabet.
  - Dùng min-heap: thêm / lấy ticket.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Hằng số nghiệp vụ
# ---------------------------------------------------------------------------

SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}

SLA: dict[str, timedelta] = {
    "critical": timedelta(minutes=30),
    "high": timedelta(hours=2),
    "medium": timedelta(hours=8),
    "low": timedelta(hours=24),
}


# ---------------------------------------------------------------------------
# Lỗi: mỗi loại một class để API trả thông báo rõ ràng cho người dùng
# ---------------------------------------------------------------------------

class TicketError(ValueError):
    """Lỗi chung của module ticket."""


class InvalidSeverityError(TicketError):
    pass


class NaiveDatetimeError(TicketError):
    """Thời gian không có múi giờ -> không biết 09:00 là giờ ở đâu."""


class DuplicateTicketError(TicketError):
    pass


# ---------------------------------------------------------------------------
# Dữ liệu
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueuedTicket:
    """Ticket đã được nhân viên xác nhận, sẵn sàng vào hàng đợi."""

    ticket_id: str
    received_at: datetime
    severity: str
    deadline_at: datetime
    summary: str = ""

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK[self.severity]

    def sort_key(self) -> tuple:
        # Dấu trừ: heap lấy giá trị NHỎ nhất, mà ta muốn severity CAO lên trước.
        return (self.deadline_at, -self.severity_rank, self.received_at, self.ticket_id)


def compute_deadline(received_at: datetime, severity: str) -> datetime:
    """Tính hạn xử lý = thời điểm nhận + SLA của mức khẩn cấp."""
    if severity not in SLA:
        raise InvalidSeverityError(
            f"Mức khẩn cấp '{severity}' không hợp lệ. Chỉ nhận: {', '.join(SLA)}"
        )
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise NaiveDatetimeError(
            f"received_at '{received_at.isoformat()}' thiếu múi giờ (ví dụ +07:00)"
        )
    return received_at + SLA[severity]


def make_ticket(ticket_id: str, received_at: datetime, severity: str, summary: str = "") -> QueuedTicket:
    """Kiểm tra dữ liệu rồi tạo ticket kèm deadline đã tính."""
    if not ticket_id or not ticket_id.strip():
        raise TicketError("ticket_id không được rỗng")
    deadline = compute_deadline(received_at, severity)
    return QueuedTicket(ticket_id.strip(), received_at, severity, deadline, summary)


# ---------------------------------------------------------------------------
# Hàng đợi ưu tiên
# ---------------------------------------------------------------------------

class TicketQueue:
    """Min-heap theo sort_key.

    Mỗi phần tử trong heap là (sort_key, ticket). ticket_id là duy nhất và
    nằm cuối sort_key, nên hai khóa không bao giờ bằng nhau.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[tuple, QueuedTicket]] = []
        # Lưu mọi mã đã từng vào hàng đợi (kể cả đã lấy ra) để chặn mã trùng.
        self._seen_ids: set[str] = set()

    def __len__(self) -> int:
        return len(self._heap)

    def add(self, ticket: QueuedTicket) -> None:
        """Thêm ticket. Quy tắc mã trùng: từ chối, không ghi đè."""
        if ticket.ticket_id in self._seen_ids:
            raise DuplicateTicketError(f"ticket_id '{ticket.ticket_id}' đã tồn tại")
        self._seen_ids.add(ticket.ticket_id)
        heapq.heappush(self._heap, (ticket.sort_key(), ticket))

    def pop_next(self) -> QueuedTicket | None:
        """Lấy ticket cần xử lý tiếp theo và xóa khỏi hàng đợi. O(log n)."""
        if not self._heap:
            return None
        _, ticket = heapq.heappop(self._heap)
        return ticket

    def peek_top(self, k: int) -> list[QueuedTicket]:
        """Xem k ticket đầu hàng đợi, KHÔNG xóa.

        heapq.nsmallest chỉ đọc list, không sửa self._heap,
        nên lần pop_next sau vẫn cho đúng kết quả.
        """
        if k < 1:
            raise TicketError("k phải >= 1")
        return [t for _, t in heapq.nsmallest(k, self._heap)]

    def ranked_view(self, k: int) -> list[dict]:
        """Top k kèm lý do xếp hạng, để hiển thị trên trang web."""
        top = self.peek_top(k)
        # Lấy thêm 1 ticket để giải thích vì sao ticket cuối đứng trên nó.
        extended = self.peek_top(k + 1) if len(self._heap) > k else top
        rows = []
        for i, ticket in enumerate(top):
            behind = extended[i + 1] if i + 1 < len(extended) else None
            rows.append({"position": i + 1, "ticket": ticket, "reason": explain(ticket, behind)})
        return rows


# ---------------------------------------------------------------------------
# Giải thích lý do xếp hạng
# ---------------------------------------------------------------------------

def explain(ticket: QueuedTicket, behind: QueuedTicket | None) -> str:
    """Giải thích vì sao `ticket` đứng trước `behind` (ticket ngay sau nó).

    Đi lần lượt theo thứ tự các phần của sort_key: phần nào khác nhau
    đầu tiên chính là lý do.
    """
    sla_minutes = int(SLA[ticket.severity].total_seconds() // 60)
    base = (
        f"Hạn {ticket.deadline_at.strftime('%Y-%m-%d %H:%M %z')} "
        f"({ticket.severity}, SLA {sla_minutes} phút từ lúc nhận)"
    )
    if behind is None:
        return base
    if ticket.deadline_at != behind.deadline_at:
        return f"{base}; hạn sớm hơn {behind.ticket_id}"
    if ticket.severity_rank != behind.severity_rank:
        return f"{base}; cùng hạn với {behind.ticket_id} nhưng khẩn cấp hơn"
    if ticket.received_at != behind.received_at:
        return f"{base}; cùng hạn và mức với {behind.ticket_id} nhưng nhận trước"
    return f"{base}; trùng hạn, mức và giờ nhận với {behind.ticket_id}, mã ticket đứng trước"
