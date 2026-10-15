import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.rag.generation.citations import build_citations
from app.rag.generation.synthesizer import build_grounded_response
from app.rag.retrieval.retriever import Retriever
from app.schemas import QueryRequest

log = logging.getLogger(__name__)
router = APIRouter()


def _sse(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n"


@router.post("/query/stream")
def query_stream(request: QueryRequest) -> StreamingResponse:
    def generate():
        try:
            yield _sse(
                {
                    "event": "status",
                    "message": "Đang truy hồi nội dung liên quan...",
                }
            )

            results = Retriever().retrieve(
                question= request.question,
                top_k=request.top_k,
                file_ids= request.file_ids,
                subject= request.subject,
                grade= request.grade,
                series= request.series,
                page_from= request.page_from,
                page_to= request.page_to
            )

            if not results:
                yield _sse(
                    {
                        "event": "not_found",
                        "message": "Không tìm thấy thông tin liên quan trong tài liệu đã index.",
                    }
                )
                return

            citations = build_citations(results)
            yield _sse(
                {
                    "event": "citations",
                    "citations": citations,
                }
            )
            yield _sse(
                {
                    "event": "status",
                    "message": "Đang tổng hợp câu trả lời...",
                }
            )
            response = build_grounded_response(question=request.question, results=results)

            for token in response.response_gen:
                if token:
                    yield _sse(
                        {
                            "event": "token",
                            "content": token,
                        }
                    )

        except Exception:
            log.exception("Query response synthesis failed.")
            yield _sse(
                {
                    "event": "error",
                    "message": "Không thể xử lý câu hỏi lúc này.",
                }
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )