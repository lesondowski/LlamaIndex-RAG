"""Quản lý vector index: khởi tạo, ingest, query, cập nhật, xóa.

Bọc VectorStoreIndex (LlamaIndex) trên Chroma. 
Sử dụng _write_lock để serialize các thao tác ghi/xóa vì Chroma embedded 
không an toàn với multi-writer (yêu cầu chạy uvicorn 1 worker).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
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
_LLM_VISIBLE_KEYS = (FILE_NAME, PAGE)
_TOP_K_MIN, _TOP_K_MAX = 1, 10


class IndexingError(RuntimeError):
    """Lỗi ghi/đọc/xóa trong vector store, bao gồm lỗi gọi API embedding."""


def _validate_top_k(top_k: int) -> None:
    """Chặn bool trước int: trong Python, isinstance(True, int) là True."""
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not _TOP_K_MIN <= top_k <= _TOP_K_MAX
    ):
        raise ValueError(
            f"top_k phải là số nguyên trong khoảng {_TOP_K_MIN}–{_TOP_K_MAX}"
        )


class IndexManager:
    """Quản lý vòng đời vector index và thao tác ghi/đọc/xóa."""

    def __init__(self) -> None:
        self._index: VectorStoreIndex | None = None
        self._embed = None
        self._init_lock = threading.Lock()
        self._write_lock = threading.Lock()

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
        if not file_ids:
            return None
        if len(file_ids) == 1:
            return MetadataFilters(filters=[MetadataFilter(key=FILE_ID, value=file_ids[0], operator=FilterOperator.EQ)])
        return MetadataFilters(filters=[MetadataFilter(key=FILE_ID, value=list(file_ids), operator=FilterOperator.IN)])

    def _resolve_filters(self, file_ids: list[str] | None, scope: QueryScope | None) -> MetadataFilters | None:
        if scope is not None and file_ids is not None:
            raise ValueError("Chỉ truyền một trong hai: file_ids hoặc scope")
        if scope is not None:
            return build_filters(scope)
        return self._build_filters(file_ids)

    def ingest_nodes(self, nodes: list[BaseNode], file_id: str, file_name: str) -> int:
        """Gắn metadata và ghi đè toàn bộ chunks vào Chroma (Full Ingestion)."""
        if not nodes:
            raise IndexingError("Không có chunk nào để index.")

        self._ensure_ready()

        for node in nodes:
            if node.metadata.get(PAGE) is None:
                raise IndexingError("Node thiếu page metadata; không thể bảo đảm citation.")

        for i, node in enumerate(nodes):
            node.id_ = f"{file_id}_{i:05d}"
            node.metadata[FILE_ID] = file_id
            node.metadata[FILE_NAME] = file_name
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

    def incremental_reindex_nodes(self, new_nodes: list[BaseNode], file_id: str, file_name: str) -> dict:
        """Cập nhật tài liệu thông minh (Incremental Re-index).
        
        Thuật toán Diffing (So sánh mã Hash):
        - Giữ nguyên ID & Vector của các chunk không đổi nội dung (tiết kiệm chi phí API).
        - Chỉ gọi Gemini Embed cho các chunk bị sửa đổi hoặc thêm mới.
        - Xóa các chunk cũ không còn tồn tại trong file mới (dọn rác).
        """
        if not new_nodes:
            raise IndexingError("Không có chunk nào để re-index.")

        self._ensure_ready()
        collection = vs.get_collection()

        # 1. Lấy dữ liệu cũ từ ChromaDB
        old_data = collection.get(where={"file_id": file_id}, include=["documents"])
        old_ids = old_data.get("ids", [])
        old_docs = old_data.get("documents", [])
        
        # 2. Tạo Map: MD5(Nội dung cũ) -> ID cũ
        old_hash_to_id = {}
        for i, doc_text in enumerate(old_docs):
            if doc_text:
                h = hashlib.md5(doc_text.strip().encode('utf-8')).hexdigest()
                old_hash_to_id[h] = old_ids[i]

        new_ids_to_keep = set()
        nodes_to_embed = []
        
        # 3. Phân tích Diffing
        for node in new_nodes:
            current_hash = hashlib.md5(node.text.strip().encode('utf-8')).hexdigest()
            
            node.metadata[FILE_ID] = file_id
            node.metadata[FILE_NAME] = file_name
            node.metadata["source"] = file_name
            keys = list(node.metadata.keys())
            node.excluded_embed_metadata_keys = keys
            node.excluded_llm_metadata_keys = [k for k in keys if k not in _LLM_VISIBLE_KEYS]

            if current_hash in old_hash_to_id:
                # NỘI DUNG KHÔNG ĐỔI -> Giữ lại Vector cũ
                reused_id = old_hash_to_id[current_hash]
                new_ids_to_keep.add(reused_id)
                # Cập nhật lại metadata (phòng trường hợp user đổi tag Môn/Lớp)
                collection.update(ids=[reused_id], metadatas=[node.metadata])
            else:
                # NỘI DUNG MỚI/SỬA -> Cần gọi API Embed
                # Dùng uuid sinh ID mới để tránh xung đột với ID cũ đang giữ
                node.id_ = f"{file_id}_{uuid.uuid4().hex[:8]}" 
                nodes_to_embed.append(node)

        # 4. Tìm các ID cũ cần xóa (có trong file cũ nhưng không có trong file mới)
        ids_to_delete = list(set(old_ids) - new_ids_to_keep)

        # 5. Thực thi ghi/xóa an toàn
        with self._write_lock:
            try:
                if ids_to_delete:
                    collection.delete(ids=ids_to_delete)
                
                if nodes_to_embed:
                    for start in range(0, len(nodes_to_embed), INGEST_BATCH_SIZE):
                        batch = nodes_to_embed[start:start + INGEST_BATCH_SIZE]
                        self._index.insert_nodes(batch)
            except Exception as exc:
                log.exception("Incremental re-index thất bại — dọn dẹp chunk mới")
                try:
                    if nodes_to_embed:
                        rollback_ids = [n.id_ for n in nodes_to_embed]
                        collection.delete(ids=rollback_ids)
                except Exception:
                    log.exception("Rollback dọn dẹp thất bại")
                raise IndexingError(f"Không thể cập nhật tài liệu: {exc}") from exc

        return {
            "total_chunks": len(new_nodes),
            "kept_chunks": len(new_ids_to_keep),
            "embedded_chunks": len(nodes_to_embed),
            "deleted_chunks": len(ids_to_delete)
        }

    def update_metadata(self, file_id: str, new_tags: dict) -> int:
        """Cập nhật nhãn trực tiếp vào Chroma, KHÔNG gọi lại Embed API."""
        with self._write_lock:
            try:
                return vs.update_metadata_by_file_id(file_id, new_tags)
            except Exception as exc:
                raise IndexingError(f"Không thể cập nhật metadata: {exc}") from exc

    def delete_by_file_id(self, file_id: str) -> int:
        """Xóa mọi chunk của file_id khỏi ChromaDB."""
        with self._write_lock:
            try:
                return vs.delete_by_file_id(file_id)
            except Exception as exc:
                raise IndexingError(f"Không thể xóa chunk của {file_id}: {exc}") from exc

    def query(self, question: str, top_k: int = 5, file_ids: list[str] | None = None, scope: QueryScope | None = None) -> list:
        _validate_top_k(top_k)
        filters = self._resolve_filters(file_ids, scope)
        self._ensure_ready()

        try:
            retriever = self._index.as_retriever(similarity_top_k=top_k, filters=filters)
            return retriever.retrieve(question)
        except Exception as exc:
            raise IndexingError(f"Truy xuất thất bại: {exc}") from exc

    def as_retriever(self, top_k: int = 5, file_ids: list[str] | None = None, scope: QueryScope | None = None) -> BaseRetriever:
        _validate_top_k(top_k)
        filters = self._resolve_filters(file_ids, scope)
        self._ensure_ready()
        return self._index.as_retriever(similarity_top_k=top_k, filters=filters)


@lru_cache(maxsize=1)
def get_index_manager() -> IndexManager:
    return IndexManager()

def __getattr__(name: str):
    if name == "index_manager":
        return get_index_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")