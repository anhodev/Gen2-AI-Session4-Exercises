"""Test schema + kiểm tra kết quả GenAI và bản người dùng sửa của Bài 04. Không cần API key."""

import pytest
from pydantic import ValidationError

from app.core.llm import load_samples
from app.modules.plans.schemas import MAX_TASKS, ExtractRequest, PlanExtraction
from app.modules.plans.validation import (
    assign_ids,
    evidence_in_text,
    format_id,
    has_errors,
    validate_extraction,
    validate_plan,
)

SAMPLES = load_samples("plans")
TEXT = SAMPLES[0]["input"]  # biên bản ví dụ trong đề


def good_output(**overrides) -> dict:
    """JSON mẫu trong đề, có thể đè từng ô để tạo ca sai."""
    data = {
        "tasks": [
            {"id": "A", "title": "Chốt yêu cầu API", "owner": "Product"},
            {"id": "B", "title": "Duyệt thiết kế UI", "owner": "Design"},
            {"id": "C", "title": "Tích hợp frontend", "owner": "Engineering"},
            {"id": "D", "title": "Kiểm thử end-to-end", "owner": "QA"},
        ],
        "dependencies": [
            {"before": "A", "after": "C", "evidence": "Sau khi chốt API mới tích hợp frontend"},
            {"before": "B", "after": "C", "evidence": "Duyệt UI trước khi tích hợp"},
            {"before": "C", "after": "D", "evidence": "Kiểm thử sau khi tích hợp"},
        ],
    }
    data.update(overrides)
    return data


def user_plan(tasks=None, dependencies=None) -> dict:
    """Bản người dùng gửi lên sau khi backend đã cấp mã T01…T04."""
    return {
        "tasks": tasks if tasks is not None else [
            {"id": "T01", "title": "Chốt yêu cầu API", "owner": "Product"},
            {"id": "T02", "title": "Duyệt thiết kế UI", "owner": "Design"},
            {"id": "T03", "title": "Tích hợp frontend", "owner": "Engineering"},
            {"id": "T04", "title": "Kiểm thử end-to-end", "owner": "QA"},
        ],
        "dependencies": dependencies if dependencies is not None else [
            {"before": "T01", "after": "T03", "evidence": "Sau khi chốt API mới tích hợp frontend"},
            {"before": "T02", "after": "T03", "evidence": "Duyệt UI trước khi tích hợp"},
            {"before": "T03", "after": "T04", "evidence": "Kiểm thử sau khi tích hợp"},
        ],
    }


def errors(issues) -> set[str]:
    return {i.field for i in issues if i.level == "error"}


def warnings(issues) -> set[str]:
    return {i.field for i in issues if i.level == "warning"}


def pairs(plan) -> list[tuple[str, str]]:
    return [(d.before, d.after) for d in plan.dependencies]


# --- Mẫu JSON gửi cho GenAI -------------------------------------------------

def test_json_schema_matches_exercise():
    schema = PlanExtraction.model_json_schema()
    assert list(schema["properties"]) == ["tasks", "dependencies"]
    assert schema["additionalProperties"] is False
    task = schema["$defs"]["TaskExtraction"]
    dep = schema["$defs"]["DependencyExtraction"]
    assert list(task["properties"]) == ["id", "title", "owner"]
    assert list(dep["properties"]) == ["before", "after", "evidence"]
    assert {"type": "null"} in task["properties"]["owner"]["anyOf"]  # owner được phép null
    assert set(task["required"]) == {"id", "title", "owner"}  # ô null vẫn phải ghi rõ
    assert set(dep["required"]) == {"before", "after", "evidence"}  # GenAI phải có bằng chứng
    assert task["additionalProperties"] is False and dep["additionalProperties"] is False


# --- Ca thường --------------------------------------------------------------

def test_exercise_output_is_valid_and_gets_system_ids():
    plan, issues = validate_extraction(good_output(), TEXT)
    assert issues == []
    assert [(t.id, t.title) for t in plan.tasks][:2] == [("T01", "Chốt yêu cầu API"), ("T02", "Duyệt thiết kế UI")]
    assert pairs(plan) == [("T01", "T03"), ("T02", "T03"), ("T03", "T04")]
    assert plan.dependencies[0].evidence == "Sau khi chốt API mới tích hợp frontend"


def test_ids_follow_order_in_minutes_not_genai_ids():
    # GenAI dùng mã 1, 2, 3 của biên bản (mẫu 5) -> vẫn thành T01, T02, T03
    plan, _ = validate_extraction(SAMPLES[4]["llm_output"], SAMPLES[4]["input"])
    assert [t.id for t in plan.tasks] == ["T01", "T02", "T03"]
    assert pairs(plan) == [("T01", "T03")]
    assert plan.tasks[1].owner is None


def test_format_id():
    assert [format_id(1), format_id(12), format_id(123)] == ["T01", "T12", "T123"]


def test_user_plan_is_valid():
    plan, issues = validate_plan(user_plan(), TEXT)
    assert issues == []
    assert [t.id for t in plan.tasks] == ["T01", "T02", "T03", "T04"]


def test_cycle_is_not_a_validation_error():
    # D -> A tạo vòng: dữ liệu vẫn hợp lệ, thuật toán mới là phần báo cycle_detected
    _, issues = validate_extraction(SAMPLES[1]["llm_output"], SAMPLES[1]["input"])
    assert issues == []


# --- Câu bằng chứng ---------------------------------------------------------

def test_evidence_ignores_case_spaces_quotes_and_end_punctuation():
    assert evidence_in_text("  “SAU KHI chốt   API mới tích hợp frontend.” ", TEXT)


def test_evidence_with_decomposed_unicode_is_found():
    import unicodedata
    assert evidence_in_text(unicodedata.normalize("NFD", "Duyệt UI trước khi tích hợp"), TEXT)


def test_evidence_not_in_minutes_is_warning_only():
    deps = good_output()["dependencies"] + [{"before": "A", "after": "B", "evidence": "Chốt API xong mới duyệt UI"}]
    plan, issues = validate_extraction(good_output(dependencies=deps), TEXT)
    assert warnings(issues) == {"dependencies.3.evidence"}
    assert not has_errors(issues)
    assert ("T01", "T02") in pairs(plan)  # vẫn giữ, người dùng quyết định


def test_only_punctuation_evidence_is_not_found():
    assert not evidence_in_text(" ... ", TEXT)


def test_user_added_dependency_without_evidence_is_ok():
    deps = user_plan()["dependencies"] + [{"before": "T01", "after": "T02", "evidence": None}]
    _, issues = validate_plan(user_plan(dependencies=deps), TEXT)
    assert issues == []


# --- Ca sát giới hạn --------------------------------------------------------

def test_no_dependencies_is_warning():
    _, issues = validate_extraction(good_output(dependencies=[]), TEXT)
    assert warnings(issues) == {"dependencies"}
    assert not has_errors(issues)


def test_duplicate_dependency_counted_once():
    deps = good_output()["dependencies"] + [{"before": "A", "after": "C", "evidence": "Sau khi chốt API mới tích hợp frontend"}]
    plan, issues = validate_extraction(good_output(dependencies=deps), TEXT)
    assert warnings(issues) == {"dependencies.3"}
    assert pairs(plan) == [("T01", "T03"), ("T02", "T03"), ("T03", "T04")]


def test_duplicate_keeps_evidence_from_later_row():
    deps = [{"before": "T01", "after": "T03", "evidence": None},
            {"before": "T01", "after": "T03", "evidence": "Sau khi chốt API mới tích hợp frontend"}]
    plan, _ = validate_plan(user_plan(dependencies=deps), TEXT)
    assert [(d.before, d.after, d.evidence) for d in plan.dependencies] == [("T01", "T03", "Sau khi chốt API mới tích hợp frontend")]


def test_opposite_directions_are_not_duplicates():
    deps = [{"before": "T01", "after": "T02", "evidence": None}, {"before": "T02", "after": "T01", "evidence": None}]
    plan, issues = validate_plan(user_plan(dependencies=deps), TEXT)
    assert len(plan.dependencies) == 2 and issues == []


def test_new_task_gets_next_id():
    tasks = user_plan()["tasks"] + [{"id": None, "title": "Viết tài liệu", "owner": None}]
    plan, issues = validate_plan(user_plan(tasks=tasks), TEXT)
    assert plan.tasks[-1].id == "T05" and issues == []


def test_new_task_id_follows_largest_existing_id():
    # Đã xóa T02, T03: việc mới lấy T05, không lấp chỗ trống
    tasks = [{"id": "T01", "title": "A", "owner": None}, {"id": "T04", "title": "D", "owner": None},
             {"id": None, "title": "E", "owner": None}, {"title": "F"}]
    plan, _ = validate_plan(user_plan(tasks=tasks, dependencies=[]), TEXT)
    assert [t.id for t in plan.tasks] == ["T01", "T04", "T05", "T06"]


def test_max_tasks():
    tasks = [{"id": f"T{i:03d}", "title": f"Việc {i}", "owner": None} for i in range(1, MAX_TASKS + 1)]
    _, issues = validate_plan(user_plan(tasks=tasks, dependencies=[]), TEXT)
    assert not has_errors(issues)
    _, issues = validate_plan(user_plan(tasks=tasks + [{"title": "Thêm 1 việc"}], dependencies=[]), TEXT)
    assert errors(issues) == {"tasks"}


# --- Dữ liệu sai: GenAI -----------------------------------------------------

def test_unknown_id_is_error():
    deps = good_output()["dependencies"] + [{"before": "D", "after": "E", "evidence": "Kiểm thử sau khi tích hợp"}]
    plan, issues = validate_extraction(good_output(dependencies=deps), TEXT)
    assert errors(issues) == {"dependencies.3.after"}
    assert ("T04", "E") in pairs(plan)  # giữ mã lạ để người dùng chọn lại, không đoán


def test_self_dependency_is_error():
    deps = [{"before": "C", "after": "C", "evidence": "Kiểm thử sau khi tích hợp"}]
    _, issues = validate_extraction(good_output(dependencies=deps), TEXT)
    assert errors(issues) == {"dependencies.0.after"}


def test_duplicate_task_id_is_error_and_its_dependencies_are_not_guessed():
    tasks = good_output()["tasks"] + [{"id": "B", "title": "Viết tài liệu", "owner": None}]
    plan, issues = validate_extraction(good_output(tasks=tasks), TEXT)
    assert errors(issues) == {"tasks.4.id"}
    assert [t.id for t in plan.tasks] == ["T01", "T02", "T03", "T04", "T05"]
    assert ("B", "T03") in pairs(plan)  # B chỉ T02 hay T05? Để người dùng chọn


def test_no_tasks_is_error():
    _, issues = validate_extraction({"tasks": [], "dependencies": []}, TEXT)
    assert errors(issues) == {"tasks"}


def test_blank_title_and_owner_are_errors():
    tasks = good_output()["tasks"]
    tasks[0] = {"id": "A", "title": "   ", "owner": " "}
    _, issues = validate_extraction(good_output(tasks=tasks), TEXT)
    assert errors(issues) == {"tasks.0.title", "tasks.0.owner"}


@pytest.mark.parametrize("field, value", [
    ("tasks", [{"id": "A", "title": "x"}]),                                   # thiếu owner
    ("tasks", [{"id": "A", "title": "x", "owner": None, "deadline": "mai"}]),  # ô lạ
    ("tasks", [{"id": "", "title": "x", "owner": None}]),                     # mã rỗng
    ("tasks", [{"id": "A", "title": "x" * 201, "owner": None}]),              # tên quá dài
    ("dependencies", [{"before": "A", "after": "C"}]),                        # thiếu bằng chứng
    ("dependencies", [{"before": "A", "after": "C", "evidence": ""}]),        # bằng chứng rỗng
    ("dependencies", "A->C"),                                                 # sai kiểu
])
def test_wrong_shape_is_rejected(field, value):
    plan, issues = validate_extraction(good_output(**{field: value}), TEXT)
    assert plan is None and has_errors(issues)


def test_extra_top_level_field_is_rejected():
    plan, issues = validate_extraction({**good_output(), "order": ["A", "B", "C", "D"]}, TEXT)
    assert plan is None and errors(issues) == {"order"}  # GenAI không được tự xếp thứ tự


def test_not_an_object_is_error():
    plan, issues = validate_extraction(["A", "B"], TEXT)
    assert plan is None and errors(issues) == {"(toàn bộ)"}


def test_assign_ids_on_parsed_extraction():
    plan = assign_ids(PlanExtraction.model_validate(good_output()))
    assert [t.id for t in plan.tasks] == ["T01", "T02", "T03", "T04"]


# --- Dữ liệu sai: bản người dùng --------------------------------------------

def test_user_cannot_set_own_id():
    tasks = user_plan()["tasks"]
    tasks[0]["id"] = "API"
    _, issues = validate_plan(user_plan(tasks=tasks), TEXT)
    assert "tasks.0.id" in errors(issues)


def test_user_dependency_to_missing_task_is_error():
    # Người dùng xóa T04 nhưng còn quan hệ T03 -> T04
    tasks = user_plan()["tasks"][:3]
    _, issues = validate_plan(user_plan(tasks=tasks), TEXT)
    assert errors(issues) == {"dependencies.2.after"}


def test_user_duplicate_id_is_error():
    tasks = user_plan()["tasks"] + [{"id": "T02", "title": "Bản sao", "owner": None}]
    _, issues = validate_plan(user_plan(tasks=tasks), TEXT)
    assert errors(issues) == {"tasks.4.id"}


def test_blank_evidence_must_be_null():
    deps = [{"before": "T01", "after": "T02", "evidence": "  "}]
    _, issues = validate_plan(user_plan(dependencies=deps), TEXT)
    assert errors(issues) == {"dependencies.0.evidence"}


def test_user_plan_extra_field_is_rejected():
    plan, issues = validate_plan({**user_plan(), "run_id": 1}, TEXT)
    assert plan is None and has_errors(issues)


def test_extract_request_rejects_empty_text():
    with pytest.raises(ValidationError):
        ExtractRequest(text="")


# --- Dữ liệu mẫu ------------------------------------------------------------

def test_samples_have_unique_labels_and_inputs():
    assert len(SAMPLES) == 10
    assert len({s["label"] for s in SAMPLES}) == len({s["input"] for s in SAMPLES}) == len(SAMPLES)


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["label"] for s in SAMPLES])
def test_each_sample_matches_expectation(sample):
    plan, issues = validate_extraction(sample["llm_output"], sample["input"])
    assert plan is not None
    assert (not has_errors(issues)) == sample["expect_can_order"]
    assert ("[Lỗi]" in sample["label"]) == (not sample["expect_can_order"])


@pytest.mark.parametrize("sample", [s for s in SAMPLES if s["expect_can_order"]], ids=lambda s: s["label"])
def test_valid_sample_round_trips_as_user_plan(sample):
    # Người dùng xác nhận nguyên bản backend trả -> vẫn hợp lệ, mã giữ nguyên
    plan, _ = validate_extraction(sample["llm_output"], sample["input"])
    again, issues = validate_plan(plan.model_dump(), sample["input"])
    assert not has_errors(issues)
    assert again == plan
