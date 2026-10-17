"""Route /query/stream: validate phạm vi tìm kiếm và stream câu trả lời qua SSE.

Luồng:
  1. QueryRequest (Pydantic) — validate cú pháp: kiểu, range, strip.
  2. resolve_scope()         — validate NGỮ NGHĨA: file_id có tồn tại không,
                                page_from/page_to có hợp lệ với số file đã chọn không.
     Bước này chạy TRƯỚC StreamingResponse để trả được HTTP 422 sạch — một khi
     stream đã mở (HTTP 200), không thể đổi status code nữa.
  3. Retriever().retrieve(scope=scope) — truy hồi top-k với MetadataFilters.
  4. build_citations()       — gửi danh sách nguồn tham khảo xuống client.
  5. build_grounded_response() — stream token từ Gemini.

SSE event:
  - status     — thông báo tiến trình
  - citations  — danh sách nguồn tham khảo (trước khi stream token)
  - token      — từng phần của câu trả lời
  - not_found  — không có chunk nào khớp filter (KHÔNG phải lỗi)
  - error      — lỗi bất ngờ; log đầy đủ ở server, client chỉ thấy thông báo chung
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.rag.generation.citations import build_citations
from app.rag.generation.synthesizer import build_grounded_response
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


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    # Validate ngữ nghĩa TRƯỚC khi mở stream:
    #   - QueryRequest đã lo cú pháp (kiểu, range, strip).
    #   - resolve_scope lo phần schema không làm được:
    #       * file_id có đúng format hex 32 ký tự không
    #       * file_id có còn tồn tại trên đĩa không (available_file_ids)
    #       * page_from/page_to chỉ dùng được khi đúng 1 file
    # Nếu để trong generate(), TypeError/MetadataError sẽ bị nuốt thành event:error
    # sau khi stream đã mở → client tưởng là lỗi LLM, khó debug.
    try:
        scope = resolve_scope(
            file_ids=request.file_ids,
            subject=request.subject,
            grade=request.grade,
            series=request.series,
            page_from=request.page_from,
            page_to=request.page_to,
            available_file_ids=available_file_ids(),
        )
    except MetadataError as exc:
        # MetadataError là lỗi của CLIENT (gửi sai) → 422, không phải 500.
        raise HTTPException(422, str(exc)) from None

    def generate():
        try:
            yield _sse({
                "event": "status",
                "message": "Đang truy hồi nội dung liên quan...",
            })

            # Chỉ truyền scope — Retriever._validate_exclusive sẽ chặn nếu
            # truyền thêm file_ids. Retriever chuyển scope → MetadataFilters
            # cho Chroma qua IndexManager._resolve_filters → build_filters.
            results = Retriever().retrieve(
                question=request.question,
                top_k=request.top_k,
                scope=scope,
            )

            if not results:
                # Filter không khớp hoặc collection rỗng — không phải lỗi.
                yield _sse({
                    "event": "not_found",
                    "message": "Không tìm thấy thông tin liên quan trong phạm vi đã chọn.",
                })
                return

            yield _sse({
                "event": "citations",
                "citations": build_citations(results),
            })
            yield _sse({
                "event": "status",
                "message": "Đang tổng hợp câu trả lời...",
            })

            response = build_grounded_response(
                question=request.question,
                results=results,
            )
            for token in response.response_gen:
                if token:
                    yield _sse({
                        "event": "token",
                        "content": token,
                    })

        except Exception:
            # Stream đã mở nên không thể đổi HTTP status.
            # log.exception ghi đầy đủ traceback ở server — đây là nơi debug thật.
            # Client chỉ thấy message chung để không lộ chi tiết nội bộ.
            log.exception(
                "Query stream thất bại: question=%r, scope=%s",
                request.question[:100],
                scope.as_dict(),
            )
            yield _sse({
                "event": "error",
                "message": "Không thể xử lý câu hỏi lúc này.",
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )