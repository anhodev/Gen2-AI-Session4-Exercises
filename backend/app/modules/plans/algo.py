"""Bài 04 - Xếp thứ tự công việc bằng topological sort (Kahn + min-heap).

  - Mỗi công việc là một đỉnh, quan hệ "A trước C" là mũi tên A → C.
  - Kahn: việc không còn phải chờ ai (bậc vào = 0) thì sẵn sàng. Lấy một việc sẵn sàng ra, giảm bậc vào
      của các việc chờ nó; việc nào về 0 thì thành sẵn sàng.
  - Nhiều việc cùng sẵn sàng: min-heap chọn mã đứng trước theo alphabet, nên cùng dữ liệu luôn ra cùng kết quả,
      dù thứ tự task trong JSON khác nhau. Số trong mã so theo giá trị: T2 trước T10, T99 trước T100.
  - Không xếp hết được nghĩa là có vòng chờ nhau: trả cycle_detected, chỉ ra một vòng cụ thể
      và các việc bị kẹt. KHÔNG trả nửa danh sách như thể đó là kế hoạch hoàn chỉnh.
  - Chia đợt (làm thêm): đợt của một việc = 1 + đợt muộn nhất trong các việc nó chờ. Việc trong cùng đợt
      không chờ nhau nên làm cùng lúc được. Đây chỉ là nhóm, KHÔNG phải lịch ngày/giờ: chưa biết thời lượng
      và số người thì không thể nói đợt 2 bắt đầu lúc nào.
  - Độ phức tạp: O((V + E) log V) với V việc, E quan hệ. Chia đợt thêm O(V + E).
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from dataclasses import dataclass, field

ORDERED = "ordered"
CYCLE = "cycle_detected"

Edge = tuple[str, str]  # (before, after)


# Lỗi: mỗi loại một class để API trả thông báo rõ ràng
class PlanError(ValueError):
    """Lỗi chung của module kế hoạch."""


class DuplicateTaskError(PlanError):
    """Hai việc cùng mã."""


class UnknownTaskError(PlanError):
    """Quan hệ dùng mã không có trong danh sách việc."""


class SelfDependencyError(PlanError):
    """Việc tự chờ chính nó."""


@dataclass(frozen=True)
class Step:
    """Một việc trong thứ tự, kèm lý do đứng ở vị trí đó."""

    task_id: str
    waits_for: list[str]    # các việc phải xong trước (mũi tên đi vào)
    next_ready: str | None  # việc có mã nhỏ nhất cũng đang sẵn sàng lúc chọn việc này (xếp sau vì mã đứng sau)
    other_ready: int        # số việc sẵn sàng khác, ngoài next_ready
    stage: int = 1          # đợt sớm nhất làm được: 1 nếu không chờ ai, ngược lại 1 + đợt muộn nhất của các việc phải chờ


@dataclass(frozen=True)
class OrderResult:
    status: str                                            # ORDERED | CYCLE
    order: list[str] = field(default_factory=list)         # rỗng khi có vòng
    steps: list[Step] = field(default_factory=list)
    cycle: list[str] = field(default_factory=list)         # vd ["A", "C", "D", "A"]
    cycle_edges: list[Edge] = field(default_factory=list)  # các mũi tên tạo thành vòng
    blocked: list[str] = field(default_factory=list)       # mọi việc chưa xếp được (trong vòng hoặc chờ vòng)
    stages: list[list[str]] = field(default_factory=list)  # các đợt làm cùng lúc, vd [["A", "B"], ["C"], ["D"]]; rỗng khi có vòng
    reason: str = ""


def id_key(task_id: str) -> tuple:
    """
    Khóa so sánh mã: theo alphabet, nhưng phần số so theo giá trị.

    "A" < "B", "T2" < "T10", "T99" < "T100". re.split với nhóm bắt luôn xen kẽ chữ, số, chữ…
    nên hai khóa so cùng kiểu ở từng vị trí.
    """
    parts = re.split(r"(\d+)", task_id)
    return tuple(int(p) if i % 2 else p.casefold() for i, p in enumerate(parts)) + (task_id,)


def build_graph(task_ids: list[str], edges: list[Edge]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Kiểm tra dữ liệu và dựng đồ thị. Quan hệ ghi lặp chỉ tính một lần.

    Returns:
        tuple: (successors, predecessors), mỗi việc -> danh sách việc sau / trước nó.

    Raises:
        DuplicateTaskError, UnknownTaskError, SelfDependencyError.
    """
    # Kiểm tra lại dù validation đã làm: gọi thẳng thuật toán với dữ liệu sai cũng không ra thứ tự sai
    seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen:
            raise DuplicateTaskError(f"Mã {task_id} bị trùng")
        seen.add(task_id)

    successors: dict[str, list[str]] = {t: [] for t in task_ids}
    predecessors: dict[str, list[str]] = {t: [] for t in task_ids}
    for before, after in dict.fromkeys(edges):  # bỏ quan hệ ghi lặp, giữ thứ tự
        for ref in (before, after):
            if ref not in seen:
                raise UnknownTaskError(f"Quan hệ {before} → {after} dùng mã {ref} không có trong danh sách việc")
        if before == after:
            raise SelfDependencyError(f"Việc {before} không thể tự chờ chính nó")
        successors[before].append(after)
        predecessors[after].append(before)
    return successors, predecessors


def topological_order(task_ids: list[str], edges: list[Edge]) -> OrderResult:
    """
    Xếp thứ tự công việc sao cho mọi mũi tên đi từ việc trước sang việc sau.

    Args:
        task_ids (list[str]): Mã các việc.
        edges (list[Edge]): Các quan hệ (before, after) người dùng đã xác nhận.

    Returns:
        OrderResult: ORDERED kèm thứ tự và lý do từng bước, hoặc CYCLE kèm một vòng và các việc bị kẹt.
    """
    successors, predecessors = build_graph(task_ids, edges)
    indegree = {t: len(predecessors[t]) for t in task_ids}

    heap = [(id_key(t), t) for t in task_ids if indegree[t] == 0]
    heapq.heapify(heap)
    order: list[str] = []
    steps: list[Step] = []
    stage: dict[str, int] = {}
    while heap:
        _, current = heapq.heappop(heap)
        # Chỉ xem đỉnh heap (O(1)): sắp xếp cả heap ở mỗi bước sẽ thành O(V² log V)
        next_ready = heap[0][1] if heap else None
        order.append(current)
        # Mọi việc phải chờ đã được xếp trước (Kahn), nên đã có đợt. Tổng cả vòng lặp: O(E).
        stage[current] = 1 + max((stage[p] for p in predecessors[current]), default=0)
        steps.append(Step(
            current, sorted(predecessors[current], key=id_key), next_ready, max(len(heap) - 1, 0), stage[current],
        ))
        for nxt in successors[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, (id_key(nxt), nxt))

    if len(order) == len(task_ids):
        stages = group_stages(order, stage)
        return OrderResult(ORDERED, order, steps, stages=stages, reason=explain_order(order, edges, stages))

    placed = set(order)
    blocked = sorted((t for t in task_ids if t not in placed), key=id_key)
    cycle = find_cycle(blocked, predecessors, placed)
    return OrderResult(
        CYCLE,
        cycle=cycle,
        cycle_edges=list(zip(cycle, cycle[1:])),
        blocked=blocked,
        reason=explain_cycle(cycle, blocked),
    )


def group_stages(order: list[str], stage: dict[str, int]) -> list[list[str]]:
    """
    Gom việc theo đợt, trong mỗi đợt xếp theo mã. Phải sắp lại vì việc cùng đợt có thể ra khỏi heap
    không theo mã: vd C (đợt 2, sẵn sàng muộn) ra sau D (đợt 2, sẵn sàng sớm) dù C đứng trước D.

    Returns:
        list[list[str]]: Đợt 1, đợt 2…, vd [["A", "B"], ["C"], ["D"]].
    """
    groups: list[list[str]] = [[] for _ in range(max(stage.values(), default=0))]
    for task_id in order:
        groups[stage[task_id] - 1].append(task_id)
    return [sorted(g, key=id_key) for g in groups]


def find_cycle(blocked: list[str], predecessors: dict[str, list[str]], placed: set[str]) -> list[str]:
    """
    Tìm một vòng trong các việc chưa xếp được.

    Mỗi việc bị kẹt còn ít nhất một việc trước nó cũng bị kẹt (nếu không nó đã được xếp).
    Nên cứ đi ngược theo việc trước bị kẹt, sau tối đa V bước sẽ gặp lại một việc đã đi qua: đó là vòng.
    Đi ngược rồi đảo lại để vòng theo chiều mũi tên. O(V + E), không đệ quy.

    Returns:
        list[str]: Vòng bắt đầu và kết thúc ở cùng một việc (mã nhỏ nhất trong vòng), vd ["A", "C", "D", "A"].
    """
    position: dict[str, int] = {}
    path: list[str] = []
    current = blocked[0]
    while current not in position:
        position[current] = len(path)
        path.append(current)
        current = min((p for p in predecessors[current] if p not in placed), key=id_key)

    loop = path[position[current]:][::-1]   # đảo lại: theo chiều mũi tên
    start = loop.index(min(loop, key=id_key))
    loop = loop[start:] + loop[:start]
    return loop + [loop[0]]


# --- Lý do --------------------------------------------------------------------

def explain_order(order: list[str], edges: list[Edge], stages: list[list[str]]) -> str:
    if not edges:
        return f"Không có quan hệ trước-sau: {len(order)} việc đều bắt đầu được ngay, xếp theo mã."
    text = (
        f"Xếp được cả {len(order)} việc, mọi quan hệ trước-sau đều được giữ. "
        "Khi nhiều việc cùng bắt đầu được, chọn mã đứng trước."
    )
    if len(stages) < len(order):
        text += f" Chia được {len(stages)} đợt: việc trong cùng đợt không chờ nhau nên làm cùng lúc được."
    return text


def explain_cycle(cycle: list[str], blocked: list[str]) -> str:
    return (
        f"Có vòng chờ nhau: {' → '.join(cycle)}. "
        f"Chưa lập được thứ tự cho {len(blocked)} việc ({', '.join(blocked)}). "
        "Hãy sửa hoặc xóa một quan hệ trong vòng rồi tính lại."
    )


def explain_step(step: Step) -> str:
    """Lý do một việc đứng ở vị trí của nó, vd "Chờ A, B xong" hoặc "Bắt đầu được ngay; chọn trước B vì mã đứng trước"."""
    text = f"Chờ {', '.join(step.waits_for)} xong" if step.waits_for else "Bắt đầu được ngay"
    if step.next_ready:
        others = f" và {step.other_ready} việc khác" if step.other_ready else ""
        text += f"; chọn trước {step.next_ready}{others} vì mã đứng trước"
    return text
