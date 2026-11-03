"""Test lớp gọi GenAI dùng chung. Không gọi mạng."""

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core import llm
from app.core.config import Settings
from app.modules.tickets.schemas import TicketExtraction


def test_raw_args_read_from_tool_call():
    message = AIMessage(content="", tool_calls=[{"name": "TicketExtraction", "args": {"severity": "urgent"}, "id": "1"}])
    # Lấy JSON thô, kể cả khi giá trị sai -> để validation.py báo lỗi sau.
    assert llm.raw_args_from_message(message) == {"severity": "urgent"}


def test_raw_args_none_when_model_answers_with_text():
    assert llm.raw_args_from_message(AIMessage(content="Xin lỗi, tôi không hiểu")) is None


def test_schema_converts_to_openai_tool():
    # Đây chính là thứ with_structured_output(method="function_calling") gửi đi.
    tool = convert_to_openai_tool(TicketExtraction)
    params = tool["function"]["parameters"]
    assert tool["function"]["name"] == "TicketExtraction"
    assert params["properties"]["severity"]["enum"] == ["low", "medium", "high", "critical"]


def test_mock_matches_sample_ignoring_spaces_and_case(tmp_path):
    (tmp_path / "demo.json").write_text(
        '[{"input": "Tôi bị trừ tiền hai lần", "llm_output": {"ok": true}}]', encoding="utf-8"
    )
    result = llm.call_mock("demo", "Mã: T01\n<message>\n  TÔI bị trừ   tiền hai lần \n</message>", tmp_path)
    assert result.raw == {"ok": True}
    assert result.model == "mock" and result.error is None


def test_mock_prefers_longest_matching_sample(tmp_path):
    # Mẫu 2 chứa trọn mẫu 1 (thêm một câu): gửi mẫu 2 phải nhận kết quả của mẫu 2
    (tmp_path / "demo.json").write_text(
        '[{"input": "A trước C.", "llm_output": {"n": 1}}, {"input": "A trước C. D trước A.", "llm_output": {"n": 2}}]',
        encoding="utf-8",
    )
    assert llm.call_mock("demo", "<minutes>A trước C. D trước A.</minutes>", tmp_path).raw == {"n": 2}
    assert llm.call_mock("demo", "<minutes>A trước C.</minutes>", tmp_path).raw == {"n": 1}


def test_mock_unknown_text_returns_error(tmp_path):
    result = llm.call_mock("demo", "nội dung lạ", tmp_path)
    assert result.raw is None
    assert "mock" in result.error


def test_without_api_key_always_uses_mock(monkeypatch):
    def must_not_call(*args):
        raise AssertionError("không được gọi model thật")

    monkeypatch.setattr(llm, "call_openai", must_not_call)
    result = llm.extract_structured("tickets", TicketExtraction, "system", "nội dung lạ", Settings(openai_api_key="", mock_llm=False))
    assert result.model == "mock"


def test_with_api_key_and_mock_off_calls_openai(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "call_openai", lambda *args: called.append(args) or llm.LLMResult({}, "gpt-4o-mini", 1))
    result = llm.extract_structured("tickets", TicketExtraction, "system", "xin chào", Settings(openai_api_key="sk-test", mock_llm=False))
    assert result.model == "gpt-4o-mini"
    assert len(called) == 1
