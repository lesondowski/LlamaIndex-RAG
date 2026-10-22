import logging
import os
import re
import time
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar
from llama_index.core import get_response_synthesizer
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.llms.google_genai import GoogleGenAI
from app.rag.generation.citations import clean_excerpt
from app.rag.generation.prompt import GROUNDED_QA_TEMPLATE, GROUNDED_REFINE_TEMPLATE

log = logging.getLogger(__name__)
T = TypeVar("T")


@lru_cache(maxsize=8)
def _create_gemini_llm(model_name: str, api_key: str, fast_fail: bool = False) -> GoogleGenAI:
    # Có model dự phòng thì không tự thử lại model đang lỗi (mất vài giây mỗi lần) mà chuyển luôn.
    extra = {"max_retries": 0} if fast_fail else {}
    return GoogleGenAI(model=model_name, api_key=api_key, temperature=0.0, **extra)


def _api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY.")
    return api_key


def gemini_model_names() -> list[str]:
    """[AI] Model chính (GEMINI_LLM_MODEL) rồi tới các model dự phòng (GEMINI_FALLBACK_MODELS,
    cách nhau bởi dấu phẩy), bỏ trùng, giữ thứ tự."""
    primary = os.getenv("GEMINI_LLM_MODEL", "gemini-2.5-flash").strip()
    if not primary:
        raise ValueError("GEMINI_LLM_MODEL không được để trống.")
    fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
    return list(dict.fromkeys([primary, *fallbacks]))


def get_gemini_llm() -> GoogleGenAI:
    names = gemini_model_names()
    return _create_gemini_llm(names[0], _api_key(), len(names) > 1)


# ---------- [AI] Tự chuyển model khi hết lượt / quá tải ----------
# Gói miễn phí của Gemini giới hạn số lượt theo NGÀY và theo TỪNG model (vd. 20 lượt/ngày), nên
# khi model chính hết lượt thì chuyển sang model kế tiếp — tổng số lượt mỗi ngày được cộng dồn.
# Mã lỗi → số giây tạm bỏ qua model đó nếu Google không nói rõ phải chờ bao lâu (retryDelay).
SKIP_SECONDS = {
    404: 3600.0,   # model không tồn tại / đã bị gỡ (thường do gõ sai tên trong .env)
    429: 60.0,     # hết lượt (theo phút hoặc theo ngày)
    503: 30.0,     # Gemini đang quá tải
}
_unavailable_until: dict[str, float] = {}


def _skip_seconds(exc: Exception) -> float:
    # Lỗi 429 kèm RetryInfo: {'retryDelay': '6799s'} = còn 6799 giây nữa mới có lượt lại.
    found = re.search(r"""retryDelay['"]?\s*:\s*['"](\d+(?:\.\d+)?)s""", str(exc))
    return float(found.group(1)) if found else SKIP_SECONDS[exc.code]


def with_fallback(call: Callable[[GoogleGenAI], T]) -> T:
    """Chạy call(llm) với model chính; hết lượt (429) / quá tải (503) / không có model (404)
    thì thử model kế tiếp trong danh sách.

    Model vừa lỗi được tạm bỏ qua cho tới khi hết thời gian Google bảo chờ, để các câu hỏi
    sau không tốn thêm một lần gọi hỏng. Lỗi khác (prompt, khóa sai...) báo ngay.
    """
    names = gemini_model_names()
    api_key = _api_key()
    now = time.monotonic()
    ready = [name for name in names if _unavailable_until.get(name, 0.0) <= now]
    # Mọi model đều đang bị tạm bỏ qua → vẫn thử, model nào sắp hết hạn chờ thì thử trước.
    order = ready or sorted(names, key=lambda name: _unavailable_until[name])

    last_error = None
    for name in order:
        try:
            result = call(_create_gemini_llm(name, api_key, len(names) > 1))
        except Exception as exc:
            code = getattr(exc, "code", None)   # google.genai.errors.APIError có mã HTTP ở .code
            if code not in SKIP_SECONDS:
                raise
            wait = _skip_seconds(exc)
            _unavailable_until[name] = time.monotonic() + wait
            log.warning("Model %s lỗi %s → tạm bỏ qua %.0f giây, thử model kế tiếp.", name, code, wait)
            last_error = exc
            continue
        if name != names[0]:
            log.info("Đang dùng model dự phòng %s.", name)
        return result
    raise last_error


def build_evidence_nodes(results: list[NodeWithScore]) -> list[NodeWithScore]:
    """Chuyển retrieved nodes → evidence nodes có citation_id + original_node_id.

    Tách riêng khỏi build_grounded_response để query.py có thể:
      1. Build evidence TRƯỚC
      2. Gửi citations xuống client TRƯỚC token đầu tiên
      3. Rồi mới gọi synth trên chính evidence nodes đó

    Không phụ thuộc behavior ngầm của StreamingResponse.source_nodes.
    """
    if not results:
        raise ValueError("Không có evidence để dựng node.")

    evidence_nodes: list[NodeWithScore] = []

    for index, result in enumerate(results, start=1):
        source_node = result.node
        metadata = source_node.metadata or {}

        source = metadata.get("file_name") or metadata.get("source")
        page = metadata.get("page", metadata.get("page_label"))

        if not source:
            raise ValueError(f"Node {source_node.node_id} thiếu nguồn.")
        if page is None:
            raise ValueError(f"Node {source_node.node_id} thiếu page.")

        # Text đưa cho LLM thấy (có header Evidence [n])
        evidence_text = (
            f"Evidence [{index}]\n"
            f"File: {source}\n"
            f"Page: {page}\n"
            f"Content:\n"
            f"{source_node.text}"
        )

        # Text SẠCH hiển thị cho user trong panel citations
        # (không có header "Evidence [n] File: ... Page: ... Content:")
        # [AI] Giữ xuống dòng để bảng / danh sách hiển thị đúng — xem clean_excerpt.
        original_excerpt = clean_excerpt(source_node.get_content())

        evidence_metadata = dict(metadata)
        evidence_metadata.update({
            "citation_id": index,
            "original_node_id": source_node.node_id,
            "original_excerpt": original_excerpt,   # ← KEY MỚI
            "file_name": source,
            "page": page,
        })

        evidence_nodes.append(
            NodeWithScore(
                node=TextNode(
                    id_=f"{source_node.node_id}-evidence-{index}",
                    text=evidence_text,
                    metadata=evidence_metadata,
                ),
                score=result.score,
            )
        )

    return evidence_nodes


def build_grounded_response(
    question: str,
    evidence_nodes: list[NodeWithScore],
    llm=None,
):
    if not evidence_nodes:
        raise ValueError("Không thể tổng hợp câu trả lời khi không có evidence.")

    synthesizer = get_response_synthesizer(
        llm=llm or get_gemini_llm(),
        response_mode=ResponseMode.COMPACT,
        streaming=True,
        text_qa_template=GROUNDED_QA_TEMPLATE,
        refine_template=GROUNDED_REFINE_TEMPLATE,
    )

    return synthesizer.synthesize(query=question, nodes=evidence_nodes)