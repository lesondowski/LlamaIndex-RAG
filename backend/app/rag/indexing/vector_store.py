"""
Khởi tạo Chroma (embedded, không cần service riêng) và bọc thành
ChromaVectorStore để llama_index dùng được.

Lưu ý vận hành: PersistentClient không an toàn khi nhiều process cùng ghi vào
một thư mục — chạy uvicorn với đúng 1 worker.
"""

import logging
from functools import lru_cache

import chromadb
from llama_index.core import StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

from app.config import BASE_DIR
from app.rag.indexing.embeddings import embed_model_id

log = logging.getLogger(__name__)

CHROMA_DIR = BASE_DIR / "storage" / "chroma_db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# Một collection cho toàn bộ tài liệu. Nếu sau này cần tách loại tài liệu,
# tạo collection khác tên — không trộn nhiều loại filter vào một collection.
COLLECTION_NAME = "financial_reports"


class EmbeddingModelMismatchError(RuntimeError):
    """Collection đã lưu vector của model embedding khác với model hiện tại."""


@lru_cache(maxsize=1)
def _get_client() -> chromadb.ClientAPI:
    # PersistentClient lưu xuống đĩa, sống qua restart server. Cache lại để
    # đúng một client cho cả process, tránh mở nhiều connection đồng thời tới
    # cùng thư mục SQLite (Chroma dùng SQLite làm metadata store).
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection():
    """Lấy (hoặc tạo) collection và kiểm tra định danh embedding.

    Định danh lệch với metadata collection → raise ngay, tránh trộn hai không
    gian vector.
    """
    model_id = embed_model_id()
    collection = _get_client().get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embed_model": model_id},
    )
    stored = (collection.metadata or {}).get("embed_model")
    if stored is None:
        log.warning(
            "Collection '%s' tạo trước khi có định danh embedding — nên xóa "
            "storage/chroma_db và index lại.",
            COLLECTION_NAME,
        )
    elif stored != model_id:
        raise EmbeddingModelMismatchError(
            f"Collection '{COLLECTION_NAME}' được index bằng '{stored}' nhưng "
            f"cấu hình hiện tại là '{model_id}'. Đổi lại cấu hình, hoặc xóa "
            f"storage/chroma_db và index lại toàn bộ tài liệu."
        )
    return collection


def get_vector_store() -> ChromaVectorStore:
    """Hàm duy nhất index_manager.py nên gọi để lấy vector store."""
    return ChromaVectorStore(chroma_collection=get_collection())


def get_storage_context() -> StorageContext:
    return StorageContext.from_defaults(vector_store=get_vector_store())


def count_vectors() -> int:
    """Tiện ích debug: đếm số vector hiện có."""
    return get_collection().count()


def count_by_file_id(file_id: str) -> int:
    return len(get_collection().get(where={"file_id": file_id}, include=[])["ids"])


def delete_by_file_id(file_id: str) -> int:
    """Xóa mọi chunk của file_id, trả về số chunk đã xóa.

    Dùng API native của Chroma vì ChromaVectorStore.delete() của llama_index
    chỉ nhận ref_doc_id, không nhận metadata filter.
    """
    n = count_by_file_id(file_id)
    if n:
        get_collection().delete(where={"file_id": file_id})
    return n


def list_file_ids(page_size: int = 1000) -> set[str]:
    """Tập file_id đang có trong index — dùng để phát hiện chunk mồ côi."""
    collection = get_collection()
    found: set[str] = set()
    offset = 0
    while True:
        batch = collection.get(include=["metadatas"], limit=page_size, offset=offset)
        metas = batch["metadatas"] or []
        if not metas:
            return found
        found.update(m["file_id"] for m in metas if m and "file_id" in m)
        offset += len(metas)