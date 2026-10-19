"""Route /query/stream: validate phạm vi tìm kiếm và stream câu trả lời qua SSE."""
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.rag.generation.citations import build_citations
from app.rag.generation.synthesizer import (
    build_grounded_response,
    build_evidence_nodes,           # ← MỚI
)
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
        raise HTTPException(422, str(exc)) from None

    def generate():
        try:
            yield _sse({
                "event": "status",
                "message": "Đang truy hồi nội dung liên quan...",
            })

            results = Retriever().retrieve(
                question=request.question,
                top_k=request.top_k,
                scope=scope,
            )

            if not results:
                yield _sse({
                    "event": "not_found",
                    "message": "Không tìm thấy thông tin liên quan trong phạm vi đã chọn.",
                })
                return

            # FLOW MỚI:
            # 1. Build evidence nodes TRƯỚC (không phụ thuộc synth)
            # 2. Gửi citations từ CHÍNH evidence nodes đó
            # 3. Rồi mới synth trên evidence nodes đã có
            evidence_nodes = build_evidence_nodes(results)

            yield _sse({
                "event": "citations",
                "citations": build_citations(evidence_nodes),   # ← evidence, KHÔNG phải results
            })
            yield _sse({
                "event": "status",
                "message": "Đang tổng hợp câu trả lời...",
            })

            response = build_grounded_response(
                question=request.question,
                evidence_nodes=evidence_nodes,                  # ← evidence, KHÔNG phải results
            )

            for token in response.response_gen:
                if token:
                    yield _sse({
                        "event": "token",
                        "content": token,
                    })

        except Exception:
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