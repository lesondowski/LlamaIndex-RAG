"""Lớp truy hồi: validate đầu vào và gọi IndexManager.

Hai entry point:
  - retrieve()               — chạy query, trả list[NodeWithScore].
  - as_llamaindex_retriever() — trả retriever native, chưa chạy query.

Cả hai nhận `scope` (đường chính) hoặc `file_ids` (đường cũ). Hai tham số loại
trừ nhau — truyền cả hai sẽ raise ValueError.
"""
from __future__ import annotations

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore

from app.rag.indexing.index_manager import IndexManager, get_index_manager
from app.rag.types import QueryScope


class Retriever:
    """Wrapper mỏng quanh IndexManager, thêm validation đầu vào.

    Nhận `index_manager` qua constructor để test có thể inject mock.
    Mặc định dùng singleton từ get_index_manager().
    """

    def __init__(self, index_manager: IndexManager | None = None) -> None:
        self._index_manager = index_manager or get_index_manager()

    # ------------------------------------------------------------------
    # Validate dùng chung cho cả hai entry point
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_question(question: str) -> str:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Câu hỏi không được để trống.")
        return question.strip()

    @staticmethod
    def _validate_top_k(top_k: int) -> None:
        # Chặn bool trước int: isinstance(True, int) là True → True sẽ thành 1.
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("top_k phải là số nguyên trong khoảng 1–10.")

    @staticmethod
    def _validate_file_ids(file_ids: list[str] | None) -> None:
        if file_ids is None:
            return
        for file_id in file_ids:
            if not isinstance(file_id, str) or not file_id.strip():
                raise ValueError("file_ids chứa giá trị rỗng.")

    @staticmethod
    def _validate_exclusive(file_ids: list[str] | None, scope: QueryScope | None) -> None:
        if scope is not None and file_ids is not None:
            raise ValueError("Chỉ truyền một trong hai: file_ids hoặc scope.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        file_ids: list[str] | None = None,
        scope: QueryScope | None = None,
    ) -> list[NodeWithScore]:
        """Chạy query và trả top-k chunk gần nhất.

        Args:
            question: câu hỏi người dùng.
            top_k: số chunk trả về (1–10).
            file_ids: (đường cũ) danh sách file giới hạn phạm vi.
            scope: phạm vi lọc đầy đủ (file_ids, subject, grade, series, page).
                   Nếu truyền scope thì file_ids phải None.

        Returns:
            list[NodeWithScore] — sắp xếp giảm dần theo cosine similarity.
        """
        question = self._validate_question(question)
        self._validate_top_k(top_k)
        self._validate_file_ids(file_ids)
        self._validate_exclusive(file_ids, scope)

        if scope is None:
            # Đường cũ: giữ nguyên chữ ký gọi IndexManager.query(file_ids=...).
            return self._index_manager.query(
                question=question,
                top_k=top_k,
                file_ids=file_ids,
            )
        return self._index_manager.query(
            question=question,
            top_k=top_k,
            file_ids=None,
            scope=scope,
        )

    def as_llamaindex_retriever(
        self,
        top_k: int = 5,
        file_ids: list[str] | None = None,
        scope: QueryScope | None = None,
    ) -> BaseRetriever:
        """Trả retriever native của LlamaIndex, chưa chạy query.

        Dùng khi ghép với query engine nâng cao (reranking, RetrieverQueryEngine).
        Đường chính /query/stream gọi retrieve() trực tiếp.
        """
        self._validate_top_k(top_k)
        self._validate_file_ids(file_ids)
        self._validate_exclusive(file_ids, scope)

        return self._index_manager.as_retriever(
            top_k=top_k,
            file_ids=file_ids,
            scope=scope,
        )