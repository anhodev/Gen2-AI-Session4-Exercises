"""Bài 04 - Kiểm tra kết quả GenAI và bản người dùng đã sửa.

  Lớp 1 (Pydantic):  đúng kiểu, đủ ô, không có ô lạ, chuỗi không quá dài, không quá nhiều task / quan hệ.
  Lớp 2 (nghiệp vụ): có ít nhất 1 task, tên việc không rỗng, mã task không trùng,
                     quan hệ chỉ dùng mã đã có, không có việc tự chờ chính nó,
                     quan hệ ghi lặp chỉ tính một lần, câu bằng chứng phải có trong biên bản.

Hai loại dữ liệu:
  - validate_extraction: JSON GenAI trả, mã tạm (A, B…). Sau khi kiểm tra, backend đổi sang T01, T02…
  - validate_plan: bản người dùng gửi lên, mã T01… do backend cấp. Việc mới thêm có id=null và được cấp mã kế tiếp.

Error chặn bước tính thứ tự. Warning chỉ nhắc, người dùng quyết định.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.schemas import Issue

from .schemas import DependencyIn, PlanExtraction, PlanIn, PlanOut, TaskIn

SYSTEM_ID = re.compile(r"^T\d{2,}$")
QUOTES = "\"'“”‘’«»"


def format_id(number: int) -> str:
    """1 -> "T01", 123 -> "T123"."""
    return f"T{number:02d}"


def normalize_text(value: str) -> str:
    """NFKC, gộp mọi khoảng trắng thành 1 dấu cách, chữ thường. Dùng để tìm câu bằng chứng trong biên bản."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def evidence_in_text(evidence: str, text: str) -> bool:
    """
    Câu bằng chứng có nằm trong biên bản không (không phân biệt hoa/thường, dấu cách thừa,
    dấu ngoặc kép và dấu chấm câu ở 2 đầu).
    """
    core = normalize_text(evidence).strip(QUOTES + " .,;:!?…")
    return bool(core) and core in normalize_text(text)


def layer1_errors(exc: ValidationError) -> list[Issue]:
    return [Issue(field=".".join(map(str, e["loc"])), message=e["msg"], level="error") for e in exc.errors()]


# --- Lớp 2: dùng chung cho cả 2 loại dữ liệu --------------------------------

def check_plan(tasks: list[BaseModel], dependencies: list[BaseModel], text: str) -> list[Issue]:
    """
    Kiểm tra nghiệp vụ trên danh sách task và quan hệ (mã đã có sẵn, dạng nào cũng được).

    Args:
        tasks (list): Có các ô id, title, owner.
        dependencies (list): Có các ô before, after, evidence (evidence có thể None).
        text (str): Biên bản gốc, để tìm câu bằng chứng.

    Returns:
        list[Issue]: Field dạng "tasks.1.id", "dependencies.0.after" để giao diện hiện cạnh đúng dòng.
    """
    issues: list[Issue] = []
    if not tasks:
        issues.append(Issue(field="tasks", message="Chưa có công việc nào", level="error"))

    # Task
    first_index: dict[str, int] = {}
    for i, task in enumerate(tasks):
        if not task.title.strip():
            issues.append(Issue(field=f"tasks.{i}.title", message="Tên công việc đang rỗng", level="error"))
        if task.owner is not None and not task.owner.strip():
            issues.append(Issue(field=f"tasks.{i}.owner", message="Người phụ trách đang là chuỗi rỗng. Không biết thì để null.", level="error"))
        if task.id is None:
            continue
        if task.id in first_index:
            issues.append(Issue(
                field=f"tasks.{i}.id",
                message=f"Mã {task.id} bị trùng với việc thứ {first_index[task.id] + 1} ('{tasks[first_index[task.id]].title}')",
                level="error",
            ))
        else:
            first_index[task.id] = i

    # Quan hệ
    seen: dict[tuple[str, str], int] = {}
    for i, dep in enumerate(dependencies):
        pair = (dep.before, dep.after)
        for side in ("before", "after"):
            ref = getattr(dep, side)
            if ref not in first_index:
                issues.append(Issue(field=f"dependencies.{i}.{side}", message=f"Mã {ref} không có trong danh sách công việc", level="error"))
        if dep.before == dep.after:
            issues.append(Issue(field=f"dependencies.{i}.after", message=f"Việc {dep.before} không thể tự chờ chính nó", level="error"))
        if pair in seen:
            issues.append(Issue(
                field=f"dependencies.{i}",
                message=f"Quan hệ {dep.before} → {dep.after} ghi lặp (giống dòng {seen[pair] + 1}): chỉ tính một lần",
                level="warning",
            ))
        else:
            seen[pair] = i

        if dep.evidence is not None:
            if not dep.evidence.strip():
                issues.append(Issue(field=f"dependencies.{i}.evidence", message="Câu bằng chứng đang rỗng. Người dùng tự thêm thì để null.", level="error"))
            elif not evidence_in_text(dep.evidence, text):
                issues.append(Issue(
                    field=f"dependencies.{i}.evidence",
                    message="Không tìm thấy câu này trong biên bản: có thể GenAI tự thêm quan hệ. Hãy kiểm tra hoặc xóa.",
                    level="warning",
                ))

    if tasks and not dependencies:
        issues.append(Issue(
            field="dependencies",
            message="Không có quan hệ trước-sau nào: mọi việc đều bắt đầu được ngay, thứ tự chỉ xếp theo mã",
            level="warning",
        ))
    return issues


def dedupe(dependencies: list[DependencyIn]) -> list[DependencyIn]:
    """Bỏ quan hệ ghi lặp, giữ dòng đầu tiên. Dòng đầu không có bằng chứng thì lấy bằng chứng của dòng sau."""
    kept: dict[tuple[str, str], DependencyIn] = {}
    for dep in dependencies:
        pair = (dep.before, dep.after)
        if pair not in kept:
            kept[pair] = dep
        elif kept[pair].evidence is None and dep.evidence is not None:
            kept[pair] = kept[pair].model_copy(update={"evidence": dep.evidence})
    return list(kept.values())


# --- JSON GenAI trả ---------------------------------------------------------

def assign_ids(parsed: PlanExtraction) -> PlanOut:
    """
    Đổi mã tạm của GenAI sang T01, T02… theo thứ tự task trong JSON, đổi luôn các quan hệ.

    Mã tạm bị trùng hoặc không có trong danh sách thì quan hệ giữ nguyên mã tạm đó. Lớp 2 sẽ báo
    "mã không có trong danh sách", để người dùng tự chọn đúng việc thay vì backend đoán.
    """
    counts: dict[str, int] = {}
    for task in parsed.tasks:
        counts[task.id] = counts.get(task.id, 0) + 1

    mapping: dict[str, str] = {}
    tasks = []
    for n, task in enumerate(parsed.tasks, start=1):
        new_id = format_id(n)
        if counts[task.id] == 1:
            mapping[task.id] = new_id
        tasks.append(TaskIn(id=new_id, title=task.title, owner=task.owner))

    dependencies = [
        DependencyIn(before=mapping.get(d.before, d.before), after=mapping.get(d.after, d.after), evidence=d.evidence)
        for d in parsed.dependencies
    ]
    return PlanOut(tasks=tasks, dependencies=dedupe(dependencies))


def validate_extraction(raw: Any, text: str) -> tuple[PlanOut | None, list[Issue]]:
    """
    Kiểm tra JSON GenAI trả về rồi cấp mã T01….

    Args:
        raw (Any): JSON thô.
        text (str): Biên bản gốc.

    Returns:
        tuple[PlanOut | None, list[Issue]]: Kế hoạch đã cấp mã (None nếu sai khuôn) và các vấn đề
        tính theo mã tạm của GenAI. Kế hoạch vẫn được trả khi có lỗi để người dùng sửa.
    """
    if not isinstance(raw, dict):
        return None, [Issue(field="(toàn bộ)", message="GenAI không trả về JSON object", level="error")]
    try:
        parsed = PlanExtraction.model_validate(raw)
    except ValidationError as exc:
        return None, layer1_errors(exc)

    issues = check_plan(parsed.tasks, parsed.dependencies, text)
    return assign_ids(parsed), issues


# --- Bản người dùng đã sửa --------------------------------------------------

def validate_plan(raw: Any, text: str) -> tuple[PlanOut | None, list[Issue]]:
    """
    Kiểm tra bản người dùng gửi lên. Việc mới thêm (id=null) được cấp mã kế tiếp.

    Args:
        raw (Any): JSON dạng PlanIn.
        text (str): Biên bản gốc, để tìm câu bằng chứng.

    Returns:
        tuple[PlanOut | None, list[Issue]]: Kế hoạch đã cấp mã, bỏ quan hệ ghi lặp (None nếu sai khuôn),
        và các vấn đề. Có lỗi "error" thì chưa được tính thứ tự.
    """
    try:
        parsed = PlanIn.model_validate(raw)
    except ValidationError as exc:
        return None, layer1_errors(exc)

    issues: list[Issue] = []
    for i, task in enumerate(parsed.tasks):
        if task.id is not None and not SYSTEM_ID.match(task.id):
            issues.append(Issue(field=f"tasks.{i}.id", message=f"Mã '{task.id}' không phải mã hệ thống cấp (T01, T02…)", level="error"))

    # Cấp mã cho việc mới: số lớn nhất đang có + 1
    used = [int(t.id[1:]) for t in parsed.tasks if t.id and SYSTEM_ID.match(t.id)]
    next_number = max(used, default=0) + 1
    tasks = []
    for task in parsed.tasks:
        if task.id is None:
            task = task.model_copy(update={"id": format_id(next_number)})
            next_number += 1
        tasks.append(task)

    issues += check_plan(tasks, parsed.dependencies, text)
    return PlanOut(tasks=tasks, dependencies=dedupe(parsed.dependencies)), issues


def has_errors(issues: list[Issue]) -> bool:
    """True nếu có ít nhất một Issue level="error"."""
    return any(i.level == "error" for i in issues)
