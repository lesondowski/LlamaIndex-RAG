"""Route /query/stream: validate phạm vi tìm kiếm và stream câu trả lời qua SSE.

Câu hỏi nối tiếp ("Còn chu vi của nó?") được viết lại thành câu tự đủ nghĩa dựa trên lịch
sử client gửi kèm (xem rag/generation/condense.py), rồi dùng câu đó cho cả bước tìm kiếm
lẫn bước tổng hợp câu trả lời.
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.rag.generation.citations import build_citations
from app.rag.generation.condense import condense_question
from app.rag.generation.synthesizer import build_evidence_nodes, build_grounded_response
from app.rag.retrieval.filters import resolve_scope
from app.rag.retrieval.retriever import Retriever
from app.rag.types import MetadataError
from app.routers.upload import available_file_ids
from app.schemas import QueryRequest

log = logging.getLogger(__name__)
router = APIRouter()


def _sse(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n"


def _error_message(exc: Exception) -> str:
    """Lỗi phía Gemini thì nói rõ nguyên nhân, thay vì một câu chung chung."""
    code = getattr(exc, "code", None)   # google.genai.errors.APIError có mã HTTP ở .code
    if code == 429:
        return "Model Gemini đã hết lượt gọi. Hãy thử lại sau, hoặc đổi GEMINI_LLM_MODEL trong backend/.env."
    if code == 503:
        return "Gemini đang quá tải. Hãy thử lại sau ít phút."
    if code == 404:
        return "Không tìm thấy model Gemini. Hãy kiểm tra GEMINI_LLM_MODEL trong backend/.env."
    return "Không thể xử lý câu hỏi lúc này."


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    # Chỉ tìm trong sách đã tải lên (storage/uploads). Không chọn nguồn = mọi sách đã tải lên,
    # không phải cả collection (tránh lẫn các chunk thử nghiệm do pipeline.py ghi vào).
    available = available_file_ids()
    if not available:
        message = "Chưa có tài liệu nào. Hãy tải sách lên ở cột Tài liệu rồi hỏi lại nhé."
        return StreamingResponse(
            iter([_sse({"event": "not_found", "message": message})]), media_type="text/event-stream",
        )

    try:
        scope = resolve_scope(
            file_ids=request.file_ids if request.file_ids is not None else sorted(available),
            subject=request.subject,
            grade=request.grade,
            series=request.series,
            page_from=request.page_from,
            page_to=request.page_to,
            available_file_ids=available,
        )
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None

    def generate():
        try:
            # Query transformation: có lịch sử → viết lại câu hỏi nối tiếp thành câu tự đủ nghĩa.
            question = request.question
            if request.history:
                yield _sse({"event": "status", "message": "Đang hiểu câu hỏi theo mạch hội thoại..."})
                question = condense_question(request.question, request.history)

            yield _sse({
                "event": "status",
                "message": "Đang truy hồi nội dung liên quan..." if question == request.question
                else f"Đang tìm: “{question}”",
            })

            results = Retriever().retrieve(question=question, top_k=request.top_k, scope=scope)
            if not results:
                yield _sse({
                    "event": "not_found",
                    "message": "Không tìm thấy thông tin liên quan trong phạm vi đã chọn.",
                })
                return

            # Dựng evidence trước, gửi trích dẫn từ CHÍNH evidence đó, rồi mới tổng hợp câu trả lời.
            evidence_nodes = build_evidence_nodes(results)
            yield _sse({"event": "citations", "citations": build_citations(evidence_nodes)})
            yield _sse({"event": "status", "message": "Đang tổng hợp câu trả lời..."})

            # Câu trả lời dùng câu hỏi ĐÃ VIẾT LẠI, khớp với evidence vừa tìm được.
            response = build_grounded_response(question=question, evidence_nodes=evidence_nodes)
            for token in response.response_gen:
                if token:
                    yield _sse({"event": "token", "content": token})

        except Exception as exc:
            log.exception("Query stream thất bại: question=%r, scope=%s", request.question[:100], scope.as_dict())
            yield _sse({"event": "error", "message": _error_message(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )