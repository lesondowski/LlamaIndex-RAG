import os
from functools import lru_cache
from llama_index.core import get_response_synthesizer
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.llms.google_genai import GoogleGenAI
from app.rag.generation.prompt import GROUNDED_QA_TEMPLATE, GROUNDED_REFINE_TEMPLATE


@lru_cache(maxsize=2)
def _create_gemini_llm(model_name: str, api_key: str) -> GoogleGenAI:
    return GoogleGenAI(model=model_name, api_key=api_key, temperature=0.0)


def get_gemini_llm() -> GoogleGenAI:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_LLM_MODEL", "gemini-2.5-flash").strip()

    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY.")
    if not model_name:
        raise ValueError("GEMINI_LLM_MODEL không được để trống.")

    return _create_gemini_llm(model_name, api_key)


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
        original_excerpt = " ".join(source_node.get_content().split())[:500]

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