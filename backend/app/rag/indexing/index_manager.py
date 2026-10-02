"""
index_manager.py — Lớp DUY NHẤT được phép thao tác với vector index.

Mọi route (/upload, /query, /files) PHẢI gọi qua đây, KHÔNG tự import
VectorStoreIndex / ChromaVectorStore trực tiếp — giữ nguyên tắc "1 lớp biết
chi tiết hạ tầng", nhất quán với api.js ở phía frontend.

Đặc điểm thiết kế
-----------------
- **Lazy init**: embed model chỉ được load ở lần đầu ingest/query. Xóa tài
  liệu KHÔNG cần embed → chạy được kể cả khi thiếu API key hoặc offline.
- **Thread-safe**: 1 lock bảo vệ quá trình init, 1 lock tuần tự hóa ghi/xóa
  (Chroma nhúng không an toàn với nhiều writer đồng thời).
- **Idempotent ingest**: node id tất định `<file_id>_<index>` + xóa chunk cũ
  trước khi chèn → upload lại cùng file không nhân đôi dữ liệu.
- **Rollback**: ingest lỗi giữa chừng thì xóa phần đã chèn, không để lại
  tài liệu nửa vời trong index.

Cách dùng
---------
    from app.rag.indexing.index_manager import get_index_manager
    mgr = get_index_manager()            # khuyến nghị
    # hoặc tương thích code cũ:
    from app.rag.indexing.index_manager import index_manager
"""
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

# Số node embed mỗi lượt gọi API — tránh gửi 1 batch quá lớn (rate limit, timeout).
INGEST_BATCH_SIZE = 64

# Chỉ những metadata key này được hiển thị cho LLM khi sinh câu trả lời.
# Các key khác (file_id, chunk_index, section_index, …) chỉ dùng để lọc/hiển thị.
_LLM_VISIBLE_KEYS = ("file_name", "page")


class IndexingError(RuntimeError):
    """Lỗi ghi / đọc / xóa trong vector store (bao gồm cả lỗi gọi API embedding)."""


class IndexManager:
    """
    Facade cho toàn bộ thao tác với vector index.

    Instance được cache qua `get_index_manager()` — dùng lại cho mọi request,
    không mở/đóng connection Chroma liên tục (tránh lock file SQLite).

    Lazy init: bản thân `__init__` KHÔNG load embed model. Lần gọi đầu tiên
    tới `ingest_nodes()` hoặc `query()` mới trigger việc load (qua
    `_ensure_ready()`). `delete_by_file_id()` không cần embed nên luôn chạy
    được ngay cả khi thiếu API key hoặc không có mạng.
    """

    def __init__(self) -> None:
        # Các field của index — điền ở _ensure_ready().
        self._index: VectorStoreIndex | None = None
        self._embed = None

        # Lock bảo vệ quá trình khởi tạo index (double-check).
        self._init_lock = threading.Lock()

        # Lock tuần tự hóa thao tác ghi/xóa — Chroma embedded chỉ an toàn với 1 writer.
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_ready(self) -> None:
        """
        Đảm bảo index đã sẵn sàng. Load embed model + tạo VectorStoreIndex ở
        lần gọi đầu tiên. An toàn khi nhiều thread cùng gọi (double-check lock).

        Chỉ ingest_nodes() và query() cần gọi hàm này. delete_by_file_id()
        KHÔNG gọi — nhờ đó xóa tài liệu không phụ thuộc vào embed model.
        """
        if self._index is not None:
            return
        with self._init_lock:
            if self._index is not None:
                return
            log.info("Khởi tạo IndexManager: load embed model lần đầu…")
            embed = get_embed_model()

            # Gán vào Settings TOÀN CỤC của LlamaIndex — thiếu bước này, một số
            # thành phần nội bộ có thể fallback về OpenAI mặc định dù embeddings.py
            # đã cấu hình provider khác (không crash, chỉ âm thầm sai).
            Settings.embed_model = embed

            # from_vector_store: GẮN vào collection Chroma đã có sẵn (hoặc rỗng nếu
            # lần đầu) — KHÔNG build lại từ đầu như from_documents().
            self._index = VectorStoreIndex.from_vector_store(
                vs.get_vector_store(),
                embed_model=embed,
            )
            self._embed = embed
            log.info("IndexManager sẵn sàng.")

    @staticmethod
    def _build_filters(file_ids: list[str] | None) -> MetadataFilters | None:
        """
        None/[] → không filter (tìm toàn bộ collection).
        1 file → EQ.
        Nhiều file → IN (một chunk chỉ cần khớp MỘT trong số đó).
        """
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
        """
        Thay thế TOÀN BỘ chunk của `file_id` bằng `nodes`.

        Idempotent: xóa chunk cũ của file_id trước khi chèn → upload lại cùng
        file không nhân đôi dữ liệu. Có rollback nếu lỗi giữa chừng.

        Args:
            nodes: danh sách TextNode từ bước chunking.
            file_id: UUID của file PDF gốc (dùng để filter khi chat).
            file_name: tên gốc hiển thị cho người dùng (citation).

        Returns:
            Số node đã index.
        """
        if not nodes:
            raise IndexingError("Không có chunk nào để index.")

        self._ensure_ready()

        # Gắn metadata trước khi chèn — để (a) filter theo file_ids lúc chat,
        # (b) hiển thị đúng tên + trang trong panel trích dẫn.
        for i, node in enumerate(nodes):
            node.id_ = f"{file_id}_{i:05d}"           # id tất định → idempotent
            node.metadata["file_id"] = file_id
            node.metadata["file_name"] = file_name
            # Ghi đè 'source' (đường dẫn .md tuyệt đối) bằng tên gốc cho citation.
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