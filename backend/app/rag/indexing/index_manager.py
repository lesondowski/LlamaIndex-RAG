"""Quản lý vector index: khởi tạo, ingest, query, xóa.

Bọc VectorStoreIndex (LlamaIndex) trên Chroma; chuyển QueryScope thành
MetadataFilters qua filters.build_filters.

Vận hành: Chroma embedded không an toàn multi-writer → chạy uvicorn 1 worker.
_write_lock serialize ghi/xóa trong process.

Hai đường filter:
  - build_filters(scope) — đường chính, đủ file_ids/subject/grade/series/page.
  - _build_filters(file_ids) — đường cũ, chỉ giữ để tương thích.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from app.rag.indexing import vector_store as vs
from app.rag.indexing.embeddings import get_embed_model
from app.rag.retrieval.filters import build_filters
from app.rag.types import FILE_ID, FILE_NAME, PAGE, QueryScope

log = logging.getLogger(__name__)

INGEST_BATCH_SIZE = 64

# Metadata LLM được thấy khi synthesize — đủ để làm citation, không gây nhiễu.
_LLM_VISIBLE_KEYS = (FILE_NAME, PAGE)

_TOP_K_MIN, _TOP_K_MAX = 1, 10


class IndexingError(RuntimeError):
    """Lỗi ghi/đọc/xóa trong vector store, bao gồm lỗi gọi API embedding."""


def _validate_top_k(top_k: int) -> None:
    """Chặn bool trước int: isinstance(True, int) là True, nếu không chặn
    thì top_k=True sẽ thành top_k=1 và qua mặt validate."""
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not _TOP_K_MIN <= top_k <= _TOP_K_MAX
    ):
        raise ValueError(
            f"top_k phải là số nguyên trong khoảng {_TOP_K_MIN}–{_TOP_K_MAX}"
        )


class IndexManager:
    """Quản lý vòng đời vector index và thao tác ghi/đọc/xóa.

    Index được khởi tạo trễ ở lần dùng đầu (không tạo kết nối khi import).
    """

    def __init__(self) -> None:
        self._index: VectorStoreIndex | None = None
        self._embed = None
        self._init_lock = threading.Lock()    # double-check khởi tạo
        self._write_lock = threading.Lock()   # serialize ghi/xóa (Chroma 1 writer)

    def _ensure_ready(self) -> None:
        """Load embed model + index một lần (fast path + lock + double-check)."""
        if self._index is not None:
            return
        with self._init_lock:
            if self._index is not None:
                return
            log.info("Khởi tạo IndexManager: load embed model lần đầu…")
            embed = get_embed_model()
            Settings.embed_model = embed
            self._index = VectorStoreIndex.from_vector_store(
                vs.get_vector_store(),
                embed_model=embed,
            )
            self._embed = embed
            log.info("IndexManager sẵn sàng.")

    @staticmethod
    def _build_filters(file_ids: list[str] | None) -> MetadataFilters | None:
        """Filter theo file_id (đường cũ). Caller mới nên dùng scope."""
        if not file_ids:
            return None
        if len(file_ids) == 1:
            return MetadataFilters(filters=[
                MetadataFilter(key=FILE_ID, value=file_ids[0], operator=FilterOperator.EQ),
            ])
        return MetadataFilters(filters=[
            MetadataFilter(key=FILE_ID, value=list(file_ids), operator=FilterOperator.IN),
        ])

    def _resolve_filters(
        self,
        file_ids: list[str] | None,
        scope: QueryScope | None,
    ) -> MetadataFilters | None:
        """Chọn đường filter; chặn mix cả hai. Guard đặt đây để query()
        và as_retriever() dùng chung, không lặp."""
        if scope is not None and file_ids is not None:
            raise ValueError("Chỉ truyền một trong hai: file_ids hoặc scope")
        if scope is not None:
            return build_filters(scope)
        return self._build_filters(file_ids)

    def ingest_nodes(self, nodes: list[BaseNode], file_id: str, file_name: str) -> int:
        """Gắn metadata và ghi chunks vào Chroma. Trả về số chunk đã ghi.

        Idempotent (id tất định) và rollback (xóa chunk khi lỗi giữa chừng).
        Raises IndexingError khi nodes rỗng, thiếu page, hoặc ghi lỗi.
        """
        if not nodes:
            raise IndexingError("Không có chunk nào để index.")

        self._ensure_ready()

        # Thiếu page thì không tạo được citation → dừng trước khi ghi vector.
        for node in nodes:
            if node.metadata.get(PAGE) is None:
                raise IndexingError(
                    "Node thiếu page metadata; không thể bảo đảm citation."
                )

        for i, node in enumerate(nodes):
            node.id_ = f"{file_id}_{i:05d}"   # id tất định → re-index ghi đè
            node.metadata[FILE_ID] = file_id
            node.metadata[FILE_NAME] = file_name
            node.metadata["source"] = file_name

            # Loại metadata khỏi text embed (tránh nhiễu); LLM chỉ thấy file_name + page.
            keys = list(node.metadata.keys())
            node.excluded_embed_metadata_keys = keys
            node.excluded_llm_metadata_keys = [
                k for k in keys if k not in _LLM_VISIBLE_KEYS
            ]

        with self._write_lock:
            try:
                vs.delete_by_file_id(file_id)   # xóa chunk cũ (nếu có)
                for start in range(0, len(nodes), INGEST_BATCH_SIZE):
                    batch = nodes[start:start + INGEST_BATCH_SIZE]
                    self._index.insert_nodes(batch)
            except Exception as exc:
                log.exception("Ingest thất bại cho file_id=%s — rollback", file_id)
                try:
                    vs.delete_by_file_id(file_id)
                except Exception:
                    log.exception("Rollback cũng thất bại cho file_id=%s", file_id)
                raise IndexingError(f"Không thể index tài liệu: {exc}") from exc

        return len(nodes)

    def delete_by_file_id(self, file_id: str) -> int:
        """Xóa mọi chunk của file_id. Trả về số chunk đã xóa.

        Không cần embed model — chỉ gọi Chroma API, nên chạy được khi
        thiếu API key hoặc không có mạng.
        """
        with self._write_lock:
            try:
                return vs.delete_by_file_id(file_id)
            except Exception as exc:
                raise IndexingError(
                    f"Không thể xóa chunk của {file_id}: {exc}"
                ) from exc

    def query(
        self,
        question: str,
        top_k: int = 5,
        file_ids: list[str] | None = None,
        scope: QueryScope | None = None,
    ) -> list:
        """Truy hồi top-k node gần nhất với câu hỏi.

        scope và file_ids loại trừ nhau — truyền cả hai sẽ raise ValueError.

        Returns:
            list[NodeWithScore] — sắp xếp giảm dần theo cosine similarity.
        """
        _validate_top_k(top_k)

        # Dựng filter trước khi load embed: scope sai báo lỗi ngay, không tốn
        # API Gemini; MetadataError không bị gói thành IndexingError.
        filters = self._resolve_filters(file_ids, scope)
        self._ensure_ready()

        try:
            retriever = self._index.as_retriever(
                similarity_top_k=top_k,
                filters=filters,
            )
            return retriever.retrieve(question)
        except Exception as exc:
            raise IndexingError(f"Truy xuất thất bại: {exc}") from exc

    def as_retriever(
        self,
        top_k: int = 5,
        file_ids: list[str] | None = None,
        scope: QueryScope | None = None,
    ) -> BaseRetriever:
        """Trả về retriever native của LlamaIndex, chưa chạy query.

        Dùng khi ghép với query engine nâng cao (reranking, RetrieverQueryEngine).
        Đường chính /query/stream gọi query() trực tiếp.
        """
        _validate_top_k(top_k)

        filters = self._resolve_filters(file_ids, scope)
        self._ensure_ready()

        return self._index.as_retriever(
            similarity_top_k=top_k,
            filters=filters,
        )


# ----------------------------------------------------------------------
# Singleton accessors
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_index_manager() -> IndexManager:
    """Trả về đúng 1 instance dùng chung cho toàn process."""
    return IndexManager()


def __getattr__(name: str):
    """Tương thích ngược cho `from ...index_manager import index_manager`.

    Nhờ PEP 562, import tên này không tạo instance ngay — chỉ tạo khi gọi
    tới, và vẫn đi qua cache của get_index_manager().
    """
    if name == "index_manager":
        return get_index_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")