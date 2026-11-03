"""Bài 01 - Thuật toán xếp hàng đợi ticket.
  - Hạn xử lý (SLA) do code tính từ received_at, GenAI không đoán.
      critical 30 phút, high 2 giờ, medium 8 giờ, low 24 giờ.
  - Khóa so sánh: (deadline_at, -severity_rank, received_at, ticket_id)
      -> hạn gần nhất lên trước; cùng hạn thì mức khẩn cấp cao hơn;
         vẫn bằng thì tin đến trước; vẫn bằng thì mã ticket theo alphabet.
  - Dùng min-heap: thêm / lấy ticket.
  - Làm thêm: giao ticket cho nhân viên theo kỹ năng và số việc tối đa (assign_tickets, cuối file).
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Hằng số nghiệp vụ
SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}

SLA: dict[str, timedelta] = {
    "critical": timedelta(minutes=30),
    "high": timedelta(hours=2),
    "medium": timedelta(hours=8),
    "low": timedelta(hours=24),
}


# Lỗi: mỗi loại một class để API trả mã lỗi phù hợp (vd: trùng mã -> 409)
class TicketError(ValueError):
    """Lỗi chung của module ticket."""


class InvalidSeverityError(TicketError):
    """Mức khẩn cấp không nằm trong danh sách cho phép."""


class NaiveDatetimeError(TicketError):
    """Thời gian không có múi giờ -> không biết 09:00 là giờ ở đâu."""


class DuplicateTicketError(TicketError):
    """Mã ticket đã từng vào hàng đợi."""


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
    """
    Tính hạn xử lý = thời điểm nhận + SLA của mức khẩn cấp.

    Args:
        received_at (datetime): Thời điểm nhận ticket, bắt buộc có múi giờ.
        severity (str): Mức khẩn cấp: low, medium, high, critical.

    Raises:
        InvalidSeverityError: Mức khẩn cấp không hợp lệ.
        NaiveDatetimeError: received_at thiếu múi giờ.

    Returns:
        datetime: Hạn xử lý.
    """
    # Kiểm tra mức khẩn cấp có trong danh sách SLA không
    if severity not in SLA:
        raise InvalidSeverityError(
            f"Mức khẩn cấp '{severity}' không hợp lệ. Chỉ nhận: {', '.join(SLA)}"
        )

    # Kiểm tra thời gian có múi giờ không
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise NaiveDatetimeError(
            f"received_at '{received_at.isoformat()}' thiếu múi giờ (ví dụ +07:00)"
        )

    return received_at + SLA[severity]


def make_ticket(ticket_id: str, received_at: datetime, severity: str, summary: str = "") -> QueuedTicket:
    """
    Kiểm tra dữ liệu rồi tạo ticket kèm deadline đã tính.

    Args:
        ticket_id (str): Mã ticket hệ thống đã cấp.
        received_at (datetime): Thời điểm nhận, có múi giờ.
        severity (str): Mức khẩn cấp.
        summary (str, optional): Tóm tắt nội dung. Defaults to "".

    Raises:
        TicketError: ticket_id rỗng, hoặc lỗi từ compute_deadline.

    Returns:
        QueuedTicket: Ticket sẵn sàng đưa vào hàng đợi.
    """
    if not ticket_id or not ticket_id.strip():
        raise TicketError("ticket_id không được rỗng")

    # Code tự tính hạn, GenAI không tham gia bước này
    deadline = compute_deadline(received_at, severity)
    return QueuedTicket(ticket_id.strip(), received_at, severity, deadline, summary)


class TicketQueue:
    """
    Hàng đợi ưu tiên dùng min-heap theo sort_key.

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
        """
        Thêm ticket vào hàng đợi. O(log n).

        Args:
            ticket (QueuedTicket): Ticket đã xác nhận.

        Raises:
            DuplicateTicketError: Mã ticket đã tồn tại. Quy tắc: từ chối, không ghi đè.
        """
        if ticket.ticket_id in self._seen_ids:
            raise DuplicateTicketError(f"ticket_id '{ticket.ticket_id}' đã tồn tại")

        self._seen_ids.add(ticket.ticket_id)
        heapq.heappush(self._heap, (ticket.sort_key(), ticket))

    def pop_next(self) -> QueuedTicket | None:
        """
        Lấy ticket cần xử lý tiếp theo và xóa khỏi hàng đợi. O(log n).

        Returns:
            QueuedTicket | None: Ticket đầu hàng đợi, None nếu hàng đợi rỗng.
        """
        if not self._heap:
            return None
        _, ticket = heapq.heappop(self._heap)
        return ticket

    def peek_top(self, k: int) -> list[QueuedTicket]:
        """
        Xem k ticket đầu hàng đợi, KHÔNG xóa. O(n log k).

        heapq.nsmallest chỉ đọc list, không sửa self._heap,
        nên lần pop_next sau vẫn cho đúng kết quả.

        Args:
            k (int): Số ticket muốn xem, phải >= 1.

        Raises:
            TicketError: k < 1.

        Returns:
            list[QueuedTicket]: Tối đa k ticket, đã xếp theo thứ tự ưu tiên.
        """
        if k < 1:
            raise TicketError("k phải >= 1")
        return [t for _, t in heapq.nsmallest(k, self._heap)]

    def ranked_view(self, k: int) -> list[dict]:
        """
        Top k ticket kèm lý do xếp hạng, để hiển thị trên trang web.

        Args:
            k (int): Số ticket muốn xem.

        Returns:
            list[dict]: Mỗi phần tử gồm position, ticket, reason.
        """
        top = self.peek_top(k)

        # Lấy thêm 1 ticket để giải thích vì sao ticket cuối đứng trên nó.
        extended = self.peek_top(k + 1) if len(self._heap) > k else top

        rows = []
        for i, ticket in enumerate(top):
            behind = extended[i + 1] if i + 1 < len(extended) else None
            rows.append({"position": i + 1, "ticket": ticket, "reason": explain(ticket, behind)})
        return rows


def explain(ticket: QueuedTicket, behind: QueuedTicket | None) -> str:
    """
    Giải thích vì sao `ticket` đứng trước `behind` (ticket ngay sau nó).

    Đi lần lượt theo thứ tự các phần của sort_key: phần nào khác nhau
    đầu tiên chính là lý do.

    Args:
        ticket (QueuedTicket): Ticket cần giải thích.
        behind (QueuedTicket | None): Ticket đứng ngay sau, None nếu là ticket cuối.

    Returns:
        str: Lý do xếp hạng.
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


# --- Làm thêm: giao ticket cho nhân viên --------------------------------------
#
# Mỗi nhân viên có kỹ năng (các loại yêu cầu xử lý được: billing, technical...) và số việc tối đa.
# Xét lần lượt từ ticket gấp nhất (đúng thứ tự hàng đợi). Với mỗi ticket:
#   1. Có người đúng kỹ năng còn chỗ: giao cho người đang ít việc nhất (bằng nhau thì mã đứng trước).
#   2. Không ai còn chỗ: thử ĐỔI NGƯỜI cho các ticket đã giao để trống ra một chỗ (đường tăng cường,
#      tìm theo chiều rộng). Vd T03 (account) đang ở Chi, An cũng làm được account và còn chỗ:
#      chuyển T03 sang An, Chi nhận T05 (technical).
#   3. Đổi kiểu gì cũng không trống được chỗ: ticket này chưa có nhân viên phù hợp, ghi rõ lý do.
# Ticket đã giao không bao giờ bị bỏ ra, chỉ có thể đổi người. Nên ticket gấp luôn được ưu tiên có người,
# và ticket bị báo "chưa có người nhận" là ticket thật sự không xếp được, không phải do thứ tự chọn.
#
# Tìm đường đi trên đồ thị nhân viên: X -> Y nếu X đang giữ một ticket loại i mà Y cũng làm được loại i.
# Với n ticket, m nhân viên, k loại yêu cầu (4): mỗi ticket tốn tối đa O(k * m^2), cả lần chạy O(n * k * m^2).
# Số nhân viên thường nhỏ (vài chục) nên chạy nhanh dù hàng đợi dài.

INTENTS = ("billing", "technical", "account", "other")


class StaffError(TicketError):
    """Danh sách nhân viên không hợp lệ."""


@dataclass(frozen=True)
class Staff:
    staff_id: str
    name: str
    skills: frozenset[str]  # các intent xử lý được
    max_tickets: int        # số ticket tối đa nhận cùng lúc; 0 = tạm không nhận (vd nghỉ phép)


@dataclass(frozen=True)
class Assignment:
    ticket_id: str
    intent: str
    staff_id: str | None  # None: chưa có nhân viên phù hợp để nhận
    reason: str


@dataclass(frozen=True)
class AssignResult:
    assignments: list[Assignment]   # theo thứ tự hàng đợi
    loads: dict[str, list[str]]     # mã nhân viên -> các ticket được giao, theo thứ tự hàng đợi

    @property
    def unassigned(self) -> list[Assignment]:
        return [a for a in self.assignments if a.staff_id is None]


def check_staff(staff: list[Staff]) -> None:
    """
    Kiểm tra danh sách nhân viên trước khi giao việc.

    Raises:
        StaffError: Mã trùng hoặc rỗng, kỹ năng ngoài danh sách, số việc tối đa âm.
    """
    seen: set[str] = set()
    for person in staff:
        if not person.staff_id.strip():
            raise StaffError("Mã nhân viên không được rỗng")
        if person.staff_id in seen:
            raise StaffError(f"Mã nhân viên {person.staff_id} bị trùng")
        seen.add(person.staff_id)
        unknown = sorted(person.skills - set(INTENTS))
        if unknown:
            raise StaffError(f"{person.name}: kỹ năng {', '.join(unknown)} không có trong danh sách ({', '.join(INTENTS)})")
        if person.max_tickets < 0:
            raise StaffError(f"{person.name}: số việc tối đa không được âm")


def assign_tickets(tickets: list[tuple[str, str]], staff: list[Staff]) -> AssignResult:
    """
    Giao ticket cho nhân viên theo thứ tự hàng đợi. Chỉ tính toán, không lấy ticket ra khỏi hàng đợi.

    Args:
        tickets (list[tuple[str, str]]): (ticket_id, intent), đã xếp theo thứ tự hàng đợi (gấp nhất trước).
        staff (list[Staff]): Danh sách nhân viên.

    Raises:
        StaffError: Danh sách nhân viên không hợp lệ.

    Returns:
        AssignResult: Ai nhận ticket nào, ticket nào chưa có người nhận và vì sao.
    """
    check_staff(staff)
    people = sorted(staff, key=lambda p: id_key(p.staff_id))
    by_id = {p.staff_id: p for p in people}
    # Ai làm được loại nào (bỏ người max = 0), theo mã nhân viên để kết quả luôn giống nhau
    capable = {intent: [p.staff_id for p in people if intent in p.skills and p.max_tickets > 0] for intent in INTENTS}
    held: dict[str, list[tuple[str, str]]] = {p.staff_id: [] for p in people}  # (ticket_id, intent) đang giữ
    position = {ticket_id: n for n, (ticket_id, _) in enumerate(tickets)}

    unassigned: dict[str, str] = {}
    for ticket_id, intent in tickets:
        path = find_path(intent, capable, held, by_id)
        if path is None:
            unassigned[ticket_id] = explain_unassigned(intent, people, held)
            continue
        apply_path(path, (ticket_id, intent), held, position)

    owner = {t: staff_id for staff_id, items in held.items() for t, _ in items}
    assignments = []
    for ticket_id, intent in tickets:
        staff_id = owner.get(ticket_id)
        reason = unassigned[ticket_id] if staff_id is None else explain_assigned(by_id[staff_id], intent, held)
        assignments.append(Assignment(ticket_id, intent, staff_id, reason))
    loads = {sid: sorted((t for t, _ in items), key=position.__getitem__) for sid, items in held.items()}
    return AssignResult(assignments, loads)


def find_path(intent: str, capable: dict, held: dict, by_id: dict) -> list[tuple[str, str]] | None:
    """
    Tìm cách trống ra một chỗ cho ticket loại `intent`.

    Returns:
        list[tuple[str, str]] | None: Các bước (nhân viên, loại ticket nhận thêm), bước đầu là người nhận ticket mới.
        Mỗi bước sau: người này nhận một ticket loại đó từ người ở bước trước. None nếu không có cách nào.
    """
    def has_room(staff_id: str) -> bool:
        return len(held[staff_id]) < by_id[staff_id].max_tickets

    # 1. Có người còn chỗ: chọn người ít việc nhất, bằng nhau thì mã đứng trước
    free = [s for s in capable[intent] if has_room(s)]
    if free:
        return [(min(free, key=lambda s: (len(held[s]), id_key(s))), intent)]

    # 2. Tìm theo chiều rộng: X -> Y nếu X đang giữ ticket loại j mà Y làm được loại j
    parent: dict[str, tuple[str | None, str]] = {s: (None, intent) for s in capable[intent]}
    queue = list(capable[intent])
    for current in queue:  # queue dài thêm trong lúc duyệt, mỗi người vào tối đa 1 lần
        for kind in sorted({k for _, k in held[current]}):
            for nxt in capable[kind]:
                if nxt in parent:
                    continue
                parent[nxt] = (current, kind)
                if has_room(nxt):
                    path, node = [], nxt
                    while node is not None:
                        prev, received = parent[node]
                        path.append((node, received))
                        node = prev
                    return path[::-1]
                queue.append(nxt)
    return None


def apply_path(path: list[tuple[str, str]], ticket: tuple[str, str], held: dict, position: dict[str, int]) -> None:
    """
    Người đầu đường nhận ticket mới. Mỗi người sau nhận từ người trước một ticket đúng loại
    (ticket ít gấp nhất trong số đó, để giữ nguyên người nhận của ticket gấp hơn nếu được).
    """
    incoming = ticket
    for (staff_id, _), nxt in zip(path, path[1:] + [None]):
        held[staff_id].append(incoming)
        if nxt is None:
            break
        _, kind = nxt
        moved = max((t for t in held[staff_id] if t[1] == kind and t is not incoming), key=lambda t: position[t[0]])
        held[staff_id].remove(moved)
        incoming = moved


def explain_assigned(person: Staff, intent: str, held: dict) -> str:
    return f"{person.name} có kỹ năng {intent}, nhận {len(held[person.staff_id])}/{person.max_tickets} việc"


def explain_unassigned(intent: str, people: list[Staff], held: dict) -> str:
    skilled = [p for p in people if intent in p.skills]
    if not skilled:
        return f"Chưa có nhân viên nào có kỹ năng {intent}"
    busy = ", ".join(f"{p.name} {len(held[p.staff_id])}/{p.max_tickets}" for p in skilled)
    return (
        f"Những người có kỹ năng {intent} đều đã nhận đủ ticket gấp hơn ({busy}). "
        "Đổi người giữa các ticket cũng không trống được chỗ"
    )


def id_key(value: str) -> tuple:
    """So mã theo alphabet, phần số so theo giá trị: NV2 trước NV10."""
    return tuple(int(p) if i % 2 else p.casefold() for i, p in enumerate(re.split(r"(\d+)", value))) + (value,)
