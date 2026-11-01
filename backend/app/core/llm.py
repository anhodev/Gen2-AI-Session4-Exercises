"""Lớp gọi GenAI dùng chung cho cả 4 bài.

Gửi prompt + schema, nhận lại JSON THÔ.
Mỗi module tự kiểm tra bằng validation.py của nó, nhờ vậy dữ liệu thật và dữ liệu mock đi chung một đường kiểm tra.

Có 2 cách chạy:
  - call_openai: gọi model thật qua with_structured_output.
  - call_mock:   đọc JSON có sẵn trong samples/, không tốn tiền.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAIError
from pydantic import BaseModel

from .config import Settings, get_settings

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


@dataclass
class LLMResult:
    raw: dict | None   # JSON GenAI trả về, giữ nguyên kể cả khi sai khuôn
    model: str         # tên model, hoặc "mock"
    latency_ms: int
    error: str | None = None  # lỗi gọi model (mất mạng, hết quota...), khác với lỗi dữ liệu


def extract_structured(
    task: str,
    schema: type[BaseModel],
    system_prompt: str,
    user_prompt: str,
    settings: Settings | None = None,
) -> LLMResult:
    """
    Cho GenAI đọc văn bản và điền vào schema. Chưa có API key thì dùng mock.

    Args:
        task (str): Tên bài, cũng là tên file mẫu samples/<task>.json.
        schema (type[BaseModel]): Mẫu JSON GenAI phải điền.
        system_prompt (str): Hướng dẫn cho model.
        user_prompt (str): Nội dung cần đọc.
        settings (Settings | None, optional): Cấu hình. Defaults to get_settings().

    Returns:
        LLMResult: JSON thô, tên model, thời gian xử lý và lỗi (nếu có).
    """
    settings = settings or get_settings()
    if settings.use_mock:
        return call_mock(task, user_prompt)
    return call_openai(schema, system_prompt, user_prompt, settings)


def call_openai(schema: type[BaseModel], system_prompt: str, user_prompt: str, settings: Settings) -> LLMResult:
    """
    Gọi model thật qua with_structured_output.

    Args:
        schema (type[BaseModel]): Mẫu JSON GenAI phải điền.
        system_prompt (str): Hướng dẫn cho model.
        user_prompt (str): Nội dung cần đọc.
        settings (Settings): Cấu hình chứa API key và tên model.

    Returns:
        LLMResult: Kết quả gọi model.
    """
    # Khai báo model
    model = ChatOpenAI(model=settings.openai_model, api_key=settings.openai_api_key, timeout=30, max_retries=1)

    # include_raw=True: nhận cả câu trả lời gốc, không chỉ bản đã parse.
    # Nếu model trả sai khuôn, "parsed" sẽ là None và mất dấu vết lỗi.
    # Ta cần JSON gốc để lưu lại và cho người dùng xem/sửa.
    #
    # method="function_calling" thay cho mặc định "json_schema":
    # json_schema bật chế độ strict của OpenAI, chế độ này không nhận một số ràng buộc
    # như max_length. Ràng buộc vẫn được Pydantic kiểm tra lại ở validation.py.
    model_with_structure = model.with_structured_output(schema, method="function_calling", include_raw=True)

    start = time.perf_counter()
    try:
        response = model_with_structure.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
    except OpenAIError as exc:  # lỗi mạng, key sai, hết quota...
        return LLMResult(None, settings.openai_model, ms_since(start), error=f"Gọi model thất bại ({type(exc).__name__}): {exc}")

    raw = raw_args_from_message(response["raw"])
    error = None if raw is not None else "Model không trả JSON theo mẫu"
    return LLMResult(raw, settings.openai_model, ms_since(start), error)


def raw_args_from_message(message: AIMessage) -> dict | None:
    """
    Lấy JSON thô từ câu trả lời của model.

    Args:
        message (AIMessage): Câu trả lời gốc (response["raw"]).

    Returns:
        dict | None: JSON trong tool_calls[0].args, None nếu model trả lời bằng văn bản.
    """
    if message.tool_calls:
        return message.tool_calls[0]["args"]
    return None


def call_mock(task: str, user_prompt: str, samples_dir: Path = SAMPLES_DIR) -> LLMResult:
    """
    Trả JSON viết sẵn trong samples/<task>.json, không tốn tiền gọi model.

    Mỗi mẫu có "input" (văn bản gốc) và "llm_output" (JSON giả lập GenAI trả).
    Nếu nội dung gửi lên chứa đúng "input" của một mẫu thì trả mẫu đó.

    Args:
        task (str): Tên file mẫu.
        user_prompt (str): Nội dung cần đọc.
        samples_dir (Path, optional): Thư mục chứa mẫu. Defaults to SAMPLES_DIR.

    Returns:
        LLMResult: Kết quả giả lập, model="mock".
    """
    prompt = normalize(user_prompt)
    for sample in load_samples(task, samples_dir):
        if normalize(sample["input"]) in prompt:
            return LLMResult(sample["llm_output"], "mock", 0)

    return LLMResult(
        None, "mock", 0,
        error="Chế độ mock chỉ đọc được nội dung mẫu. Hãy chọn một mẫu, hoặc đặt OPENAI_API_KEY và MOCK_LLM=false.",
    )


def load_samples(task: str, samples_dir: Path = SAMPLES_DIR) -> list[dict]:
    """Đọc danh sách mẫu của một bài, trả list rỗng nếu chưa có file."""
    path = samples_dir / f"{task}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def normalize(text: str) -> str:
    """Bỏ dấu cách thừa, không phân biệt hoa/thường."""
    return " ".join(text.split()).casefold()


def ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
