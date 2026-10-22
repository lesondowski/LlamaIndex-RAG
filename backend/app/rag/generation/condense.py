"""Bộ nhớ hội thoại + query transformation (condense question).

Câu hỏi nối tiếp như "Còn chu vi của nó?" không tự đủ nghĩa: đem đi tìm nguyên văn thì
retriever không biết "nó" là gì và dễ trả về đoạn sai chủ đề. Trước khi tìm, module này dùng
LLM viết lại câu hỏi dựa trên vài lượt hỏi–đáp gần nhất thành một câu độc lập
("Chu vi hình tròn tính thế nào?"). query.py dùng câu đã viết lại cho CẢ bước tìm kiếm lẫn
bước tổng hợp câu trả lời — giống CondenseQuestionChatEngine của LlamaIndex nhưng giữ
nguyên luồng streaming + citations hiện có.

Nguyên tắc: viết lại chỉ để tìm chính xác hơn, không phải bước bắt buộc. Không có lịch sử
thì bỏ qua (không tốn lần gọi LLM nào); LLM lỗi hoặc trả về kết quả vô lý thì dùng câu gốc.

Lịch sử do client gửi kèm (app.js → historyBefore), mỗi tin có role ("user"/"assistant")
và content. Nhận cả object (schemas.ChatTurn) lẫn dict để script đánh giá dùng được.

[AI] File này viết với sự hỗ trợ của Claude Code.
"""
import logging
import re
from collections.abc import Sequence

from app.rag.generation.prompt import CONDENSE_QUESTION_TEMPLATE
from app.rag.generation.synthesizer import get_gemini_llm

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------
MAX_HISTORY_MESSAGES = 6       # 3 lượt hỏi–đáp gần nhất là đủ để hiểu "nó" chỉ cái gì
MAX_CHARS_PER_MESSAGE = 1000   # câu trả lời dài chỉ cần phần đầu (thường nêu chủ đề)
MAX_REWRITE_CHARS = 500        # LLM trả về dài hơn thế là đang trả lời chứ không phải viết lại

SPEAKER = {"user": "Học sinh", "assistant": "Trợ lý"}   # khớp cách gọi trong CONDENSE_QUESTION_TEMPLATE

_CITATION_RE = re.compile(r"\s*\[\d+\]")     # marker trích dẫn [1], [2]... của câu trả lời cũ
_LABEL_RE = re.compile(r"^\s*câu hỏi độc lập\s*:\s*", re.IGNORECASE)
_QUOTES = ' "“”\'«»'


# ---------------------------------------------------------------------------
# Chuẩn bị lịch sử cho prompt
# ---------------------------------------------------------------------------
def _field(turn, name: str) -> str:
    """Đọc role/content từ object (ChatTurn, SimpleNamespace) hoặc dict."""
    value = turn.get(name) if isinstance(turn, dict) else getattr(turn, name, None)
    return str(value or "")


def _squash(text: str, limit: int) -> str:
    """Gộp mọi khoảng trắng/xuống dòng thành một dấu cách rồi cắt độ dài —
    mỗi tin nằm gọn trên một dòng "Học sinh: ..." / "Trợ lý: ..." trong prompt."""
    return " ".join(text.split())[:limit]


def format_history(history: Sequence) -> str:
    """Các tin gần nhất thành đoạn hội thoại cho prompt (cũ → mới).

    - Chỉ lấy MAX_HISTORY_MESSAGES tin cuối (cửa sổ trượt).
    - Bỏ marker [n] trong câu trả lời cũ: chúng chỉ có nghĩa với evidence của lượt đó,
      để lại dễ làm LLM nhầm sang evidence của lượt hiện tại.
    - Bỏ tin rỗng hoặc vai trò lạ (schemas.py đã chặn, đây là lớp phòng thủ thứ hai).
    """
    lines = []
    for turn in list(history)[-MAX_HISTORY_MESSAGES:]:
        speaker = SPEAKER.get(_field(turn, "role"))
        if speaker is None:
            continue
        text = _field(turn, "content")
        if speaker == SPEAKER["assistant"]:
            text = _CITATION_RE.sub("", text)
        text = _squash(text, MAX_CHARS_PER_MESSAGE)
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Làm sạch đầu ra của LLM
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    """Lấy đúng câu hỏi từ đầu ra của LLM.

    LLM đôi khi lặp lại nhãn "CÂU HỎI ĐỘC LẬP:", bọc câu trong ngoặc kép, hoặc thêm dòng
    giải thích phía sau → bỏ nhãn, bỏ ngoặc, chỉ giữ dòng đầu tiên có nội dung.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    first = _LABEL_RE.sub("", lines[0])
    return first.strip(_QUOTES)


# ---------------------------------------------------------------------------
# Hàm chính
# ---------------------------------------------------------------------------
def condense_question(question: str, history: Sequence, llm=None) -> str:
    """Trả về câu hỏi độc lập để đem đi tìm kiếm.

    Args:
        question: câu hỏi người dùng vừa gõ.
        history: các tin TRƯỚC câu hỏi này (cũ → mới), mỗi tin có role + content.
        llm: truyền LLM giả khi test; để None thì dùng model trong GEMINI_LLM_MODEL.

    Returns:
        Câu đã viết lại; hoặc nguyên câu gốc khi không có lịch sử, LLM lỗi, hay kết quả
        rỗng / quá dài (LLM đang trả lời thay vì viết lại).
    """
    chat_history = format_history(history or [])
    if not chat_history:
        return question   # câu hỏi đầu tiên: không tốn lần gọi LLM nào

    try:
        prompt = CONDENSE_QUESTION_TEMPLATE.format(chat_history=chat_history, question=question)
        response = (llm or get_gemini_llm()).complete(prompt)
        rewritten = _clean(response.text)
    except Exception:
        # Xuống cấp nhẹ nhàng: thiếu khóa, sai tên model, Gemini quá tải... → vẫn hỏi bằng câu gốc.
        log.warning("Condense question thất bại, dùng câu hỏi gốc: %r", question[:100], exc_info=True)
        return question

    if not rewritten or len(rewritten) > MAX_REWRITE_CHARS:
        log.warning("Câu hỏi viết lại rỗng hoặc quá dài (%d ký tự), dùng câu hỏi gốc.", len(rewritten))
        return question

    if rewritten == question:
        log.info("Condense question: giữ nguyên %r", question[:100])
    else:
        log.info("Condense question: %r -> %r", question[:100], rewritten[:100])
    return rewritten