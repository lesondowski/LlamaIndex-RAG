import math
import uuid

import chromadb
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.vector_stores.chroma import ChromaVectorStore

from app.rag.indexing.index_manager import IndexManager


class KeywordEmbedding(BaseEmbedding):
    def _vector(self, text: str) -> list[float]:
        normalized = text.lower()

        if "doanh thu" in normalized:
            return [1.0, 0.0, 0.0]
        if "chi phí" in normalized:
            return [0.0, 1.0, 0.0]
        if "lợi nhuận" in normalized:
            return [0.0, 0.0, 1.0]

        return [0.0, 0.0, 0.0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


def make_node(text: str, file_id: str, page: int) -> TextNode:
    return TextNode(
        text=text,
        metadata={
            "file_id": file_id,
            "file_name": f"{file_id}.pdf",
            "source": f"{file_id}.pdf",
            "page": page,
        },
    )


def make_test_manager() -> IndexManager:
    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        name=f"dense_retrieval_{uuid.uuid4().hex[:12]}",
        metadata={"hnsw:space": "cosine"},
    )
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
    )

    nodes = [
        make_node("Chi phí vận hành tăng.", "file-a", 1),
        make_node("Doanh thu tăng trong kỳ.", "file-a", 2),
        make_node("Lợi nhuận được cải thiện.", "file-b", 1),
    ]

    index = VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=KeywordEmbedding(),
    )

    # Đưa index cô lập vào manager để kiểm tra chính đường query/filter của nó.
    manager = IndexManager()
    manager._index = index
    return manager


def test_chroma_dense_retrieval_ranks_and_filters_nodes():
    manager = make_test_manager()

    all_results = manager.query(
        question="doanh thu",
        top_k=3,
    )

    assert len(all_results) == 3
    assert isinstance(all_results[0], NodeWithScore)
    assert all_results[0].node.metadata["page"] == 2
    assert all(
        result.score is not None and math.isfinite(float(result.score))
        for result in all_results
    )

    file_results = manager.query(
        question="doanh thu",
        top_k=3,
        file_ids=["file-a"],
    )

    assert len(file_results) == 2
    assert {result.node.metadata["file_id"] for result in file_results} == {
        "file-a"
    }
    assert file_results[0].node.metadata["page"] == 2

    multiple_file_results = manager.query(
        question="doanh thu",
        top_k=3,
        file_ids=["file-a", "file-b"],
    )

    assert len(multiple_file_results) == 3