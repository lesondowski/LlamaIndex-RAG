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
COLLECTION_NAME = "SGKCTST"


class EmbeddingModelMismatchError(RuntimeError):
    """Collection đã lưu vector của model embedding khác với model hiện tại."""


@lru_cache(maxsize=1)
def _get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(CHROMA_DIR)) # Chroma persist vector database xuống disk


@lru_cache(maxsize=1)
def get_collection():
    """
    - Dense Vector Retrieval: giúp tìm các vector gần query
    nhanh hơn so với việc tính khoảng cách với toàn bộ vector.
    các vector được xếp hạng dựa trên khoảng cách cosine.
    """
    model_id = embed_model_id()
    collection = _get_client().get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embed_model": model_id}, #     cấu hình HNSW với metric cosine.
    )
    metadata = collection.metadata or {}
    stored_model_id = metadata.get("embed_model")
    stored_distance = metadata.get("hnsw:space")
    if stored_model_id is None:
        if collection.count() > 0:
            raise EmbeddingModelMismatchError(
                f"Collection {COLLECTION_NAME!r} đã có vector nhưng thiếu "
                "embedding fingerprint; cần xác minh/rebuild thủ công."
            )

        if stored_distance not in (None, "cosine"):
            raise EmbeddingModelMismatchError(
                f"Collection rỗng nhưng distance là {stored_distance!r}; "
                "hãy tạo collection mới để dùng cosine."
            )

        collection.modify(
            metadata={"hnsw:space": "cosine", "embed_model": model_id}
        )
        metadata = collection.metadata or {}
        stored_model_id = metadata.get("embed_model")
        stored_distance = metadata.get("hnsw:space")

        
    if stored_model_id != model_id:
        raise EmbeddingModelMismatchError(
            f"Collection đang dùng {stored_model_id!r}, còn cấu hình hiện tại là {model_id!r} Cần dùng lại model cũ hoặc rebuild index.")

    if stored_distance != "cosine":
        raise EmbeddingModelMismatchError(
            f"Collection đang dùng distance {stored_distance!r}, không phải "
            "'cosine'. Cần tạo collection mới và index lại."
        )
    return collection


def get_vector_store() -> ChromaVectorStore:
    return ChromaVectorStore(chroma_collection=get_collection()) #tạo adapter để LlamaIndex sử dụng Chroma.


def get_storage_context() -> StorageContext:
    return StorageContext.from_defaults(vector_store=get_vector_store())


def count_vectors() -> int:
    """Tiện ích debug: đếm số vector hiện có — xác nhận ingest chạy đúng
    mà không cần mở Chroma UI riêng."""
    return get_collection().count()


def count_by_file_id(file_id: str) -> int:
    return len(get_collection().get(where={"file_id": file_id}, include=[])["ids"]) #metadata filtering.


def delete_by_file_id(file_id: str) -> int:
    """Xóa mọi chunk của file_id bằng API native của Chroma. Trả về số chunk đã xóa.
    (ChromaVectorStore.delete() của llama_index chỉ nhận ref_doc_id, không nhận filter.)"""
    n = count_by_file_id(file_id)
    if n:
        get_collection().delete(where={"file_id": file_id})
    return n


def list_file_ids(page_size: int = 1000) -> set[str]:
    """Tập file_id đang có trong index — để phát hiện chunk mồ côi."""
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
