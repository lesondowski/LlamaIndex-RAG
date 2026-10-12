import os
from functools import lru_cache
from llama_index.core import get_response_synthesizer
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.llms.google_genai import GoogleGenAI
from app.rag.generation.prompt import GROUNDED_QA_TEMPLATE,GROUNDED_REFINE_TEMPLATE


@lru_cache(maxsize=2) # có thể chỉnh lên 5-6 để nhớ nhiều hơn
def _create_gemini_llm(model_name: str, api_key: str) -> GoogleGenAI:
    return GoogleGenAI(model=model_name,api_key=api_key,temperature=0.0)


def get_gemini_llm() -> GoogleGenAI:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_LLM_MODEL", "gemini-2.5-flash").strip()

    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY.")
    if not model_name:
        raise ValueError("GEMINI_LLM_MODEL không được để trống.")

    return _create_gemini_llm(model_name, api_key)


def build_grounded_response(
    question: str,
    results: list[NodeWithScore],
    llm=None,
):
    if not results:
        raise ValueError("Không thể tổng hợp câu trả lời khi không có evidence.")

    evidence_nodes = []

    for index, result in enumerate(results, start=1):
        source_node = result.node
        metadata = source_node.metadata or {}

        source = metadata.get("file_name") or metadata.get("source")
        page = metadata.get("page", metadata.get("page_label"))

        if not source:
            raise ValueError(f"Node {source_node.node_id} thiếu nguồn.")
        if page is None:
            raise ValueError(f"Node {source_node.node_id} thiếu page.")

        evidence_text = (
            f"Evidence [{index}]\n"
            f"File: {source}\n"
            f"Page: {page}\n"
            f"Content:\n"
            f"{source_node.text}"
        )

        evidence_nodes.append(
            NodeWithScore(
                node=TextNode(
                    id_=f"{source_node.node_id}-evidence-{index}",
                    text=evidence_text,
                ),
                score=result.score,
            )
        )

    synthesizer = get_response_synthesizer(
        llm=llm or get_gemini_llm(),
        response_mode=ResponseMode.COMPACT,
        streaming=True,
        text_qa_template=GROUNDED_QA_TEMPLATE,
        refine_template=GROUNDED_REFINE_TEMPLATE,
    )

    return synthesizer.synthesize(
        query=question,
        nodes=evidence_nodes,
    )