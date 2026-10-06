"""
Vector index facade cho pipeline RAG.

Mọi thao tác với vector index — ingest, xóa, truy hồi — đều đi qua module này.
Các route và tầng khác không được import trực tiếp VectorStoreIndex hoặc
ChromaVectorStore.

Invariant
---------
- Một embedding model duy nhất được dùng cho cả ingestion và retrieval. Model
  được load lazy ở lần dùng đầu tiên và cache cho suốt vòng đời process.
- Node ID có tính tất định (``<file_id>_<index>``), nên ingest lại cùng file
  sẽ thay thế chứ không nhân đôi chunk.
- Ingest có tính nguyên tử theo từng file: hoặc mọi chunk được ghi thành công,
  hoặc khi lỗi thì không chunk nào còn lại trong index.

Ràng buộc vận hành
------------------
- Chroma embedded không an toàn với nhiều writer đồng thời. Ghi và xóa được
  tuần tự hóa qua lock.
- Xóa file không cần embedding model — endpoint xóa vẫn hoạt động kể cả khi
  thiếu API key.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.schema import BaseNode, NodeWithScore
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from app.rag.indexing import vector_store as vs
from app.rag.indexing.embeddings import get_embed_model

log = logging.getLogger(__name__)

# Số node gửi mỗi lượt insert_nodes() — chia nhỏ để rollback dễ hơn nếu lỗi
# giữa chừng. Không liên quan tới batch API embedding (việc đó do SDK xử lý).
INGEST_BATCH_SIZE = 64

# Metadata key được hiển thị cho LLM khi sinh câu trả lời. Các key khác
# (file_id, chunk_index, …) chỉ dùng để filter/hiển thị.
_LLM_VISIBLE_KEYS = ("file_name", "page")


class IndexingError(RuntimeError):
    """Lỗi ghi/đọc/xóa trong vector store, bao gồm lỗi gọi API embedding."""


class IndexManager:
    """Facade cho toàn bộ thao tác với vector index.

    Instance được cache qua get_index_manager() — dùng lại cho mọi request,
    không mở/đóng connection Chroma liên tục.

    Lazy init: __init__ không load embed model. Lần gọi đầu tiên tới
    ingest_nodes() hoặc query() mới trigger. delete_by_file_id() không cần
    embed nên luôn chạy được kể cả khi thiếu API key.
    """

    def __init__(self) -> None:
        self._index: VectorStoreIndex | None = None
        self._init_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def _ensure_ready(self) -> None:
        """Load embed model và tạo VectorStoreIndex ở lần gọi đầu tiên.

        Double-check lock: an toàn khi nhiều thread cùng gọi. Chỉ ingest_nodes()
        và query() cần gọi; delete_by_file_id() không cần.
        """
        if self._index is not None:
            return
        with self._init_lock:
            if self._index is not None:
                return
            log.info("Khởi tạo IndexManager: load embed model lần đầu…")
            embed = get_embed_model()

            # Gán vào Settings toàn cục của LlamaIndex để các thành phần nội bộ
            # dùng đúng model, không fallback về mặc định.
            Settings.embed_model = embed

            self._index = VectorStoreIndex.from_vector_store(
                vs.get_vector_store(),
                embed_model=embed,
            )
            log.info("IndexManager sẵn sàng.")

    @staticmethod
    def _build_filters(file_ids: list[str] | None) -> MetadataFilters | None:
        """None/[] → không filter; 1 file → EQ; nhiều file → IN."""
        if not file_ids:
            return None
        if len(file_ids) == 1:
            f = MetadataFilter(key="file_id", value=file_ids[0], operator=FilterOperator.EQ)
        else:
            f = MetadataFilter(key="file_id", value=list(file_ids), operator=FilterOperator.IN)
        return MetadataFilters(filters=[f])

    def ingest_nodes(self, nodes: list[BaseNode], file_id: str, file_name: str) -> int:
        """Thay thế toàn bộ chunk của `file_id` bằng `nodes`.

        Idempotent: xóa chunk cũ trước khi chèn → upload lại cùng file không
        nhân đôi dữ liệu. Rollback nếu lỗi giữa chừng.

        Returns:
            Số node đã index.
        """
        if not nodes:
            raise IndexingError("Không có chunk nào để index.")

        self._ensure_ready()

        for i, node in enumerate(nodes):
            node.id_ = f"{file_id}_{i:05d}"
            node.metadata["file_id"] = file_id
            node.metadata["file_name"] = file_name
            node.metadata["source"] = file_name

            keys = list(node.metadata.keys())
            node.excluded_embed_metadata_keys = keys
            node.excluded_llm_metadata_keys = [k for k in keys if k not in _LLM_VISIBLE_KEYS]

        with self._write_lock:
            try:
                vs.delete_by_file_id(file_id)
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
        """Xóa mọi chunk của `file_id`, trả về số chunk đã xóa.

        Không cần embed model — chỉ dùng Chroma collection API.
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
    ) -> list[NodeWithScore]:
        """Truy hồi top-k node gần nhất với câu hỏi.

        Args:
            question: câu hỏi người dùng.
            top_k: số chunk trả về.
            file_ids: None/[] → tìm toàn bộ collection; có giá trị → chỉ tìm
                trong các file đã chọn.

        Returns:
            Danh sách node kèm score, sắp xếp giảm dần theo score.
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


@lru_cache(maxsize=1)
def get_index_manager() -> IndexManager:
    """Trả về instance IndexManager dùng chung cho process hiện tại."""
    return IndexManager()


def __getattr__(name: str):
    """Tương thích code cũ: `from ...index_manager import index_manager`.

    Nhờ PEP 562, import tên này không tạo instance ngay — chỉ tạo khi thực sự
    gọi tới, và vẫn đi qua cache của get_index_manager().
    """
    if name == "index_manager":
        return get_index_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")