"""Test thuật toán Bài 04. Không gọi GenAI, không cần database.

Chia 3 nhóm theo yêu cầu nộp bài: ca thường, ca sát giới hạn, dữ liệu sai.
"""

import itertools
import random
import time

import pytest

from app.modules.plans.algo import (
    CYCLE,
    ORDERED,
    DuplicateTaskError,
    SelfDependencyError,
    UnknownTaskError,
    explain_step,
    id_key,
    topological_order,
)

TASKS = ["A", "B", "C", "D"]
EDGES = [("A", "C"), ("B", "C"), ("C", "D")]  # ví dụ trong đề


def is_valid_order(order, edges) -> bool:
    position = {t: i for i, t in enumerate(order)}
    return all(position[a] < position[b] for a, b in edges)


def is_real_cycle(cycle, edges) -> bool:
    """Vòng khép kín, mỗi bước là một mũi tên có thật, không đi qua một việc 2 lần."""
    return (
        len(cycle) >= 3 and cycle[0] == cycle[-1]
        and len(set(cycle[:-1])) == len(cycle) - 1
        and all(edge in set(edges) for edge in zip(cycle, cycle[1:]))
    )


# --- Ca thường --------------------------------------------------------------

def test_exercise_example():
    result = topological_order(TASKS, EDGES)
    assert result.status == ORDERED
    assert result.order == ["A", "B", "C", "D"]
    assert result.cycle == [] and result.blocked == []


def test_same_result_when_tasks_and_edges_are_shuffled():
    # Tự kiểm tra 1: đổi thứ tự task trong JSON vẫn cùng kết quả
    rng = random.Random(0)
    for _ in range(50):
        tasks, edges = TASKS[:], EDGES[:]
        rng.shuffle(tasks)
        rng.shuffle(edges)
        assert topological_order(tasks, edges).order == ["A", "B", "C", "D"]


def test_steps_explain_each_position():
    steps = {s.task_id: s for s in topological_order(TASKS, EDGES).steps}
    assert explain_step(steps["A"]) == "Bắt đầu được ngay; chọn trước B vì mã đứng trước"
    assert explain_step(steps["B"]) == "Bắt đầu được ngay"
    assert explain_step(steps["C"]) == "Chờ A, B xong"
    assert (steps["D"].waits_for, steps["D"].next_ready, steps["D"].other_ready) == (["C"], None, 0)


def test_step_mentions_how_many_other_tasks_were_ready():
    step = topological_order(["A", "B", "C", "D"], []).steps[0]
    assert explain_step(step) == "Bắt đầu được ngay; chọn trước B và 2 việc khác vì mã đứng trước"


def test_adding_d_to_a_reports_cycle_then_removing_it_orders_again():
    # Tự kiểm tra 2
    result = topological_order(TASKS, EDGES + [("D", "A")])
    assert result.status == CYCLE
    assert result.cycle == ["A", "C", "D", "A"]
    assert result.cycle_edges == [("A", "C"), ("C", "D"), ("D", "A")]
    assert result.order == [] and result.steps == []  # không trả nửa danh sách
    assert "A → C → D → A" in result.reason
    assert topological_order(TASKS, EDGES).status == ORDERED


def test_tasks_waiting_on_a_cycle_are_blocked_too():
    # E chờ D, D nằm trong vòng -> E cũng không xếp được; B không liên quan vòng nhưng vẫn không được trả ra
    result = topological_order(TASKS + ["E"], EDGES + [("D", "A"), ("D", "E")])
    assert result.blocked == ["A", "C", "D", "E"]
    assert result.cycle == ["A", "C", "D", "A"]
    assert result.order == []


def test_two_task_cycle():
    result = topological_order(["A", "B"], [("A", "B"), ("B", "A")])
    assert (result.status, result.cycle) == (CYCLE, ["A", "B", "A"])


def test_no_dependencies_orders_by_id():
    result = topological_order(["C", "A", "B"], [])
    assert result.order == ["A", "B", "C"]
    assert "bắt đầu được ngay" in result.reason


def test_qr_launch_plan():
    # Mẫu 6: A chốt yêu cầu, B thiết kế, C API QR, D hợp đồng, E giao diện, F cổng ngân hàng, G kiểm thử
    edges = [("A", "B"), ("A", "C"), ("B", "E"), ("C", "E"), ("D", "F"), ("C", "F"), ("E", "G"), ("F", "G")]
    result = topological_order(list("ABCDEFG"), edges)
    assert result.order == ["A", "B", "C", "D", "E", "F", "G"]
    assert is_valid_order(result.order, edges)


def test_ready_task_with_smaller_id_jumps_ahead():
    # C sẵn sàng sau khi B xong, và đứng trước D (đã sẵn sàng từ đầu) vì mã nhỏ hơn
    result = topological_order(["B", "C", "D"], [("B", "C")])
    assert result.order == ["B", "C", "D"]


# --- Chia đợt (làm thêm) ----------------------------------------------------

def is_valid_stages(stages, tasks, edges) -> bool:
    """Đủ mọi việc, mỗi việc một lần; mũi tên luôn đi sang đợt sau; mỗi việc ở đợt sớm nhất có thể."""
    stage = {t: n for n, group in enumerate(stages, start=1) for t in group}
    if sorted(stage) != sorted(tasks) or sum(map(len, stages)) != len(tasks):
        return False
    if any(stage[a] >= stage[b] for a, b in edges):
        return False
    before = {t: {a for a, b in edges if b == t} for t in tasks}
    # Đợt sớm nhất: đợt 1 thì không chờ ai; đợt k > 1 thì chờ ít nhất một việc ở đợt k - 1
    return all(
        (not before[t]) if stage[t] == 1 else any(stage[p] == stage[t] - 1 for p in before[t])
        for t in tasks
    )


def test_exercise_stages():
    result = topological_order(TASKS, EDGES)
    assert result.stages == [["A", "B"], ["C"], ["D"]]
    assert [s.stage for s in result.steps] == [1, 1, 2, 3]
    assert "Chia được 3 đợt" in result.reason


def test_qr_launch_plan_stages():
    edges = [("A", "B"), ("A", "C"), ("B", "E"), ("C", "E"), ("D", "F"), ("C", "F"), ("E", "G"), ("F", "G")]
    result = topological_order(list("ABCDEFG"), edges)
    # F chờ D (đợt 1) và C (đợt 2) nên ở đợt 3, không phải đợt 2
    assert result.stages == [["A", "D"], ["B", "C"], ["E", "F"], ["G"]]


def test_no_dependencies_is_one_stage():
    result = topological_order(["C", "A", "B"], [])
    assert result.stages == [["A", "B", "C"]]


def test_chain_has_one_task_per_stage_and_no_stage_note():
    result = topological_order(["A", "B", "C"], [("A", "B"), ("B", "C")])
    assert result.stages == [["A"], ["B"], ["C"]]
    assert "đợt" not in result.reason  # không có gì làm cùng lúc được thì không nói


def test_stage_is_sorted_by_id_even_if_ready_later():
    # D chờ A, C chờ Z: D ra khỏi heap trước C nhưng cùng đợt 2, trong đợt vẫn xếp theo mã
    result = topological_order(["A", "C", "D", "Z"], [("A", "D"), ("Z", "C")])
    assert result.order == ["A", "D", "Z", "C"]
    assert result.stages == [["A", "Z"], ["C", "D"]]


def test_cycle_has_no_stages():
    result = topological_order(TASKS, EDGES + [("D", "A")])
    assert result.stages == []


@pytest.mark.parametrize("seed", range(100))
def test_stages_are_valid_on_random_graphs(seed):
    rng = random.Random(1000 + seed)
    tasks = [f"T{i:02d}" for i in range(1, rng.randint(1, 12) + 1)]
    # Chỉ nối từ mã nhỏ sang mã lớn để không có vòng, rồi xáo thứ tự
    edges = [(a, b) for i, a in enumerate(tasks) for b in tasks[i + 1:] if rng.random() < 0.25]
    rng.shuffle(tasks)
    rng.shuffle(edges)
    result = topological_order(tasks, edges)
    assert is_valid_stages(result.stages, tasks, edges)
    assert all(group == sorted(group, key=id_key) for group in result.stages)


# --- Ca sát giới hạn --------------------------------------------------------

def test_numbers_in_ids_compare_by_value():
    ids = ["T10", "T2", "T100", "T99", "T01"]
    assert sorted(ids, key=id_key) == ["T01", "T2", "T10", "T99", "T100"]
    assert topological_order(ids, []).order == ["T01", "T2", "T10", "T99", "T100"]


def test_id_key_is_alphabetical_for_letters():
    assert sorted(["b", "A", "C", "a"], key=id_key) == ["A", "a", "b", "C"]
    assert sorted(["1", "A", "B2", "B10"], key=id_key) == ["1", "A", "B2", "B10"]


def test_single_task():
    assert topological_order(["A"], []).order == ["A"]


def test_empty_plan():
    assert topological_order([], []).status == ORDERED


def test_duplicate_edge_counted_once():
    result = topological_order(["A", "B"], [("A", "B"), ("A", "B")])
    assert result.order == ["A", "B"]
    assert result.steps[1].waits_for == ["A"]


def test_isolated_tasks_and_chain_together():
    result = topological_order(["Z", "B", "A"], [("Z", "A")])
    assert result.order == ["B", "Z", "A"]


def test_long_chain_cycle_has_no_recursion_limit():
    n = 50_000
    ids = [f"T{i}" for i in range(n)]
    edges = [(ids[i], ids[i + 1]) for i in range(n - 1)] + [(ids[-1], ids[0])]
    result = topological_order(ids, edges)
    assert result.status == CYCLE and len(result.cycle) == n + 1


def test_cycle_found_inside_bigger_blocked_area():
    # Vòng B -> C -> B, còn A chờ C và D chờ A: tất cả bị kẹt, vòng chỉ là B, C
    result = topological_order(["A", "B", "C", "D"], [("C", "A"), ("B", "C"), ("C", "B"), ("A", "D")])
    assert result.cycle == ["B", "C", "B"]
    assert result.blocked == ["A", "B", "C", "D"]


# --- Dữ liệu sai ------------------------------------------------------------

def test_unknown_task_is_rejected():
    with pytest.raises(UnknownTaskError):
        topological_order(TASKS, EDGES + [("D", "E")])


def test_self_dependency_is_rejected():
    with pytest.raises(SelfDependencyError):
        topological_order(TASKS, [("C", "C")])


def test_duplicate_task_id_is_rejected():
    with pytest.raises(DuplicateTaskError):
        topological_order(["A", "B", "A"], [])


# --- Đối chiếu brute force và tốc độ ----------------------------------------

def brute_force(tasks, edges):
    """Thử mọi hoán vị theo thứ tự alphabet: hoán vị hợp lệ đầu tiên là đáp án Kahn + heap phải ra."""
    for perm in itertools.permutations(sorted(tasks, key=id_key)):
        if is_valid_order(perm, edges):
            return list(perm)
    return None  # không có thứ tự nào: có vòng


def random_graph(rng, n, p):
    tasks = [chr(ord("A") + i) for i in range(n)]
    rng.shuffle(tasks)
    edges = [(a, b) for a in tasks for b in tasks if a != b and rng.random() < p]
    return tasks, edges


@pytest.mark.parametrize("seed", range(200))
def test_matches_brute_force_on_random_graphs(seed):
    rng = random.Random(seed)
    tasks, edges = random_graph(rng, rng.randint(1, 6), rng.choice([0.1, 0.2, 0.35]))
    expected = brute_force(tasks, edges)
    result = topological_order(tasks, edges)
    if expected is None:
        assert result.status == CYCLE
        assert is_real_cycle(result.cycle, edges)
        assert set(result.cycle) <= set(result.blocked)
    else:
        assert (result.status, result.order) == (ORDERED, expected)


def test_10000_tasks_run_fast():
    rng = random.Random(1)
    ids = [f"T{i:05d}" for i in range(10_000)]
    edges = [(ids[a], ids[b]) for a, b in ((rng.randrange(10_000), rng.randrange(10_000)) for _ in range(50_000)) if a < b]
    rng.shuffle(ids)
    started = time.perf_counter()
    result = topological_order(ids, edges)
    assert time.perf_counter() - started < 1.0  # O((V + E) log V)
    assert result.status == ORDERED and is_valid_order(result.order, edges)
    assert is_valid_stages(result.stages, ids, edges)
