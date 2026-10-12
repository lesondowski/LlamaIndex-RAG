from __future__ import annotations
from llama_index.core.schema import NodeWithScore
from app.rag.indexing.index_manager import get_index_manager
from app.rag.indexing.index_manager import IndexManager


class Retriever:

    def __init__(self, index_manager: IndexManager | None = None) -> None:
        if index_manager is None:
            index_manager = get_index_manager()

        self._index_manager = index_manager


    def retrieve(self, question: str, top_k: int = 5, file_ids: list[str] | None = None) -> list[NodeWithScore]:
        
        if not isinstance(question, str) or not question.strip():
            raise ValueError(" ----- !!! Câu hỏi trống !!! -----")

        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("Lỗi: -- Top K nằm ngoài khoảng từ 1 - 10 !!! ")

        if file_ids is not None:
            for file_id in file_ids:
                if not isinstance(file_id, str) or not file_id.strip():
                    raise ValueError("Lỗi: file_ids chứa giá trị rỗng")
        
        
        return self._index_manager.query(question=question.strip(), top_k=top_k, file_ids=file_ids)

    def as_llamaindex_retriever(self, top_k:int = 5, file_ids: list[str] | None = None):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("top_k phải nằm trong khoảng 1 đến 10.")

        if file_ids is not None:
            for file_id in file_ids:
                if not isinstance(file_id, str) or not file_id.strip():
                    raise ValueError("file_ids rỗng.")

        return self._index_manager.as_retriever( top_k=top_k,file_ids=file_ids)

