from __future__ import annotations
import logging
import threading
from functools import lru_cache
from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from app.rag.indexing import vector_store as vs
from app.rag.indexing.embeddings import get_embed_model

log = logging.getLogger(__name__)
INGEST_BATCH_SIZE = 64
_LLM_VISIBLE_KEYS = ("file_name", "page")


class IndexingError(RuntimeError):
    """Lỗi ghi / đọc / xóa trong vector store (bao gồm cả lỗi gọi API embedding)."""


class IndexManager:
    def __init__(self) -> None:
        # Các field của index — điền ở _ensure_ready().
        self._index: VectorStoreIndex | None = None
        self._embed = None

        # Lock bảo vệ quá trình khởi tạo index (double-check).
        self._init_lock = threading.Lock()

        # Lock tuần tự hóa thao tác ghi/xóa — Chroma embedded chỉ an toàn với 1 writer.
        self._write_lock = threading.Lock()

    def _ensure_ready(self) -> None:
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

        if not file_ids:
            return None
        if len(file_ids) == 1:
            f = MetadataFilter(key="file_id", value=file_ids[0], operator=FilterOperator.EQ)
        else:
            f = MetadataFilter(key="file_id", value=list(file_ids), operator=FilterOperator.IN)
        return MetadataFilters(filters=[f])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def ingest_nodes(self, nodes: list[BaseNode], file_id: str, file_name: str) -> int:
        if not nodes:
            raise IndexingError("Không có chunk nào để index.")

        self._ensure_ready()

        for node in nodes:
            if node.metadata.get("page") is None:
                raise IndexingError(
                    "Node thiếu page metadata; không thể bảo đảm citation."
                )

        for i, node in enumerate(nodes):
            node.id_ = f"{file_id}_{i:05d}"           # id tất định → idempotent
            node.metadata["file_id"] = file_id
            node.metadata["file_name"] = file_name  
            node.metadata["source"] = file_name

            # Loại metadata khỏi văn bản đem embed — tránh nhiễu vector.
            # LLM chỉ thấy file_name + page.
            keys = list(node.metadata.keys())
            node.excluded_embed_metadata_keys = keys
            node.excluded_llm_metadata_keys = [k for k in keys if k not in _LLM_VISIBLE_KEYS]

        with self._write_lock:
            try:
                vs.delete_by_file_id(file_id)         # xóa chunk cũ (nếu có)
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
        """
        Xóa mọi chunk của `file_id` khỏi vector store. Trả về số chunk đã xóa.

        KHÔNG cần embed model — chỉ dùng Chroma collection API. Vì vậy thao
        tác này chạy được kể cả khi thiếu API key hoặc không có mạng.
        """
        with self._write_lock:
            try:
                return vs.delete_by_file_id(file_id)
            except Exception as exc:
                raise IndexingError(f"Không thể xóa chunk của {file_id}: {exc}") from exc

    def query(
        self,
        question: str,
        top_k: int = 5,
        file_ids: list[str] | None = None,
    ) -> list:
        """
        Truy hồi top-k node gần nhất với câu hỏi.

        Args:
            question: câu hỏi của người dùng.
            top_k: số chunk trả về.
            file_ids: None/[] → tìm toàn bộ collection (kho dữ liệu chung).
                      Có giá trị → chỉ tìm trong các file đã chọn.

        Returns:
            list[NodeWithScore] — sắp xếp giảm dần theo score (cosine similarity).
        
        sử dụng kỹ thuật trong dense vector retrieval
        """
        self._ensure_ready()
        try:
            retriever = self._index.as_retriever(
                similarity_top_k=top_k,
                filters=self._build_filters(file_ids),
            )
            return retriever.retrieve(question)
        except Exception as exc:
            raise IndexingError(f"Truy xuất thất bại: {exc}") from exc

    def as_retriever(self, top_k: int = 5, file_ids: list[str] | None =  None):
        # tạo retriever native, xài cơ chế filter sẵn có
        self._ensure_ready()
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("Top_k phải nằm trng khoảng từ 1 - 10")

        return self._index.as_retriever(similarity_top_k=top_k, filters=self._build_filters(file_ids))


# ----------------------------------------------------------------------
# Singleton accessors
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_index_manager() -> IndexManager:
    """Trả về đúng 1 instance dùng chung cho toàn process."""
    return IndexManager()


def __getattr__(name: str):
    """
    Tương thích ngược cho code cũ `from ...index_manager import index_manager`.

    Nhờ PEP 562, việc import tên này KHÔNG tạo instance ngay — chỉ tạo khi
    thực sự gọi tới, và vẫn đi qua cache của get_index_manager().
    """
    if name == "index_manager":
        return get_index_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")