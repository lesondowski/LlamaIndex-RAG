import os
from functools import lru_cache

from llama_index.core.embeddings import BaseEmbedding
from llama_index.embeddings.jinaai import JinaEmbedding


def _embedding_config() -> tuple[str, str, str]:
    """Trích xuất cấu hình embedding từ biến môi trường.

    Returns:
        tuple: (provider, model_name, api_key)
    """
    provider = os.getenv("EMBED_PROVIDER", "jina").strip().lower()
    # Mặc định dùng Jina v3 (1024 dimensions), hỗ trợ đa ngôn ngữ (bao gồm tiếng Việt)
    model_name = os.getenv("JINA_EMBED_MODEL", "jina-embeddings-v3").strip()
    api_key = os.getenv("JINA_API_KEY", "").strip()
    return provider, model_name, api_key


def embed_model_id() -> str:
    """Tạo ID định danh cho mô hình embedding hiện tại (VD: 'jina:jina-embeddings-v3').

    Mục đích: Hàm này được dùng trong `vector_store.py` để lưu làm "dấu vân tay" (fingerprint)
    vào metadata của ChromaDB. Nếu ai đó vô tình đổi biến môi trường sang model khác
    (VD: từ Gemini sang Jina) mà không rebuild lại index, ChromaDB sẽ phát hiện ID không khớp
    và chặn truy vấn, tránh việc tìm kiếm bằng vector rác (do số chiều vector bị lệch).
    """
    provider, model_name, _ = _embedding_config()
    if provider != "jina":
        raise ValueError(f"Embedding provider chưa được hỗ trợ: {provider}")
    return f"{provider}:{model_name}"


@lru_cache(maxsize=2)
def _create_jina_embedding(model_name: str, api_key: str) -> BaseEmbedding:
    """Khởi tạo instance của JinaEmbedding.

    Sử dụng `lru_cache` để đảm bảo mô hình chỉ được khởi tạo một lần duy nhất
    trong suốt vòng đời của ứng dụng (tránh việc mỗi request lại tạo một kết nối mới).

    Lưu ý: Jina v3 hỗ trợ tham số `task` (VD: 'retrieval.passage' cho tài liệu và
    'retrieval.query' cho câu hỏi) để tối ưu hóa độ chính xác. Tuy nhiên, LlamaIndex
    mặc định dùng chung một instance cho cả hai pha (ingest và query), nên ta để
    mặc định (text-matching) để đảm bảo tính nhất quán.
    """
    return JinaEmbedding(
        api_key=api_key,
        model_name=model_name,
    )


def get_embed_model() -> BaseEmbedding:
    """Factory function: Trả về instance của mô hình embedding.

    Thực hiện validate các biến môi trường bắt buộc trước khi khởi tạo
    để báo lỗi sớm (fail-fast) thay vì để ứng dụng crash giữa chừng khi query.
    """
    provider, model_name, api_key = _embedding_config()

    if provider != "jina":
        raise ValueError(f"Embedding provider chưa được hỗ trợ: {provider}")
    if not model_name:
        raise ValueError("JINA_EMBED_MODEL không được để trống.")
    if not api_key:
        raise ValueError("Thiếu JINA_API_KEY trong environment.")

    return _create_jina_embedding(model_name, api_key)