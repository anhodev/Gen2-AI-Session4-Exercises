"""Bài 04 - Schema (mẫu dữ liệu).
  1. PlanExtraction: mẫu JSON GenAI phải điền từ biên bản họp (task + dependency).
  2. PlanIn: bản người dùng đã xem/sửa, gửi lên để kiểm tra và tính thứ tự.
  3. Request / Response của API.

Mã task do backend cấp (T01, T02… theo thứ tự xuất hiện trong biên bản), chỉ có nghĩa trong một kế hoạch.
GenAI chỉ đặt mã tạm (A, B, C…) để nối task với dependency; backend đổi sang T01… sau khi kiểm tra.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import Issue

TEMP_ID_MAX = 20     # mã tạm GenAI đặt
TITLE_MAX = 200
OWNER_MAX = 100
EVIDENCE_MAX = 300
MAX_TASKS = 100
MAX_DEPENDENCIES = 500


# 1. Mẫu GenAI phải điền
class TaskExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        min_length=1,
        max_length=TEMP_ID_MAX,
        description=(
            "Mã tạm để nối task với dependency. Biên bản đã đánh mã (A, B, 1, 2…) thì dùng đúng mã đó, "
            "nếu không thì đặt A, B, C… theo thứ tự xuất hiện. Không dùng tên việc làm mã. Mỗi task một mã khác nhau."
        ),
    )
    title: str = Field(..., min_length=1, max_length=TITLE_MAX, description="Tên công việc, ngắn gọn, giữ ý của biên bản.")
    owner: str | None = Field(
        ..., max_length=OWNER_MAX, description="Người hoặc nhóm phụ trách. null nếu biên bản không nói."
    )


class DependencyExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: str = Field(..., min_length=1, max_length=TEMP_ID_MAX, description="Mã task phải xong trước.")
    after: str = Field(..., min_length=1, max_length=TEMP_ID_MAX, description="Mã task chỉ làm được sau khi task 'before' xong.")
    evidence: str = Field(
        ...,
        min_length=1,
        max_length=EVIDENCE_MAX,
        description=(
            "Chép nguyên văn câu (hoặc một đoạn câu) trong biên bản cho thấy quan hệ trước-sau này. "
            "Không có câu nào như vậy thì KHÔNG được tạo dependency."
        ),
    )


class PlanExtraction(BaseModel):
    """Danh sách việc và quan hệ trước-sau GenAI đọc ra từ biên bản họp."""

    # extra="forbid": GenAI trả thêm ô lạ thì báo lỗi, không lặng lẽ bỏ qua.
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskExtraction] = Field(..., max_length=MAX_TASKS, description="Các công việc trong biên bản.")
    dependencies: list[DependencyExtraction] = Field(
        ...,
        max_length=MAX_DEPENDENCIES,
        description=(
            "Quan hệ trước-sau: 'before' phải xong rồi mới làm 'after'. Chỉ ghi khi biên bản nói rõ, kèm câu bằng chứng. "
            "Không tự suy ra quan hệ từ kinh nghiệm. Không có quan hệ nào thì để []."
        ),
    )


# 2. Bản người dùng đã xem/sửa
class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(None, max_length=TEMP_ID_MAX, description="Mã backend đã cấp (T01…). null với việc mới thêm.")
    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    owner: str | None = Field(None, max_length=OWNER_MAX)


class DependencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: str = Field(..., min_length=1, max_length=TEMP_ID_MAX)
    after: str = Field(..., min_length=1, max_length=TEMP_ID_MAX)
    evidence: str | None = Field(None, max_length=EVIDENCE_MAX, description="null với quan hệ người dùng tự thêm, không có câu trong biên bản.")


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskIn] = Field(..., max_length=MAX_TASKS)
    dependencies: list[DependencyIn] = Field(..., max_length=MAX_DEPENDENCIES)


# 3. API
class ExtractRequest(BaseModel):
    """POST /plans/extract: nội dung biên bản họp."""

    text: str = Field(min_length=1, max_length=20000)


class PlanOut(BaseModel):
    """Kế hoạch đã chuẩn hóa: mã T01…, quan hệ ghi lặp chỉ còn một."""

    tasks: list[TaskIn]
    dependencies: list[DependencyIn]


class ExtractResponse(BaseModel):
    run_id: int | None = None
    llm_output: dict | None   # JSON GenAI trả về (mã tạm A, B…), giữ nguyên để người dùng xem
    llm_issues: list[Issue]   # vấn đề của JSON gốc, theo mã tạm và vị trí trong llm_output (vd "Mã B bị trùng")
    plan: PlanOut | None      # đã đổi sang mã T01…, None nếu sai khuôn
    issues: list[Issue]       # kiểm tra lại plan, theo mã T01… và vị trí trong plan: gắn vào đúng dòng người dùng sửa
    can_order: bool           # plan không còn lỗi (mã lạ, tự chờ chính nó…)


class ValidateOut(BaseModel):
    plan: PlanOut | None       # None nếu sai khuôn
    issues: list[Issue]
    can_order: bool            # không có lỗi "error"
