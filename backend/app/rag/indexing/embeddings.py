"""
embeddings.py — Cấu hình model embedding dùng chung cho toàn bộ pipeline.

MỤC ĐÍCH: Đảm bảo CHỈ CÓ 1 model embedding được dùng xuyên suốt — từ lúc
ingest (index) tới lúc query (retrieval). Nếu 2 bước dùng model khác nhau,
vector sinh ra không cùng không gian: cosine similarity vẫn chạy được,
KHÔNG báo lỗi, nhưng kết quả retrieval sai một cách rất khó phát hiện
(đây là loại bug nguy hiểm nhất — âm thầm, không crash).

Vì vậy get_embed_model() ở đây PHẢI là nơi DUY NHẤT mọi module khác gọi
tới — không module nào được tự khởi tạo embedding model riêng.
"""
import os
from functools import lru_cache

from llama_index.core.embeddings import BaseEmbedding

# "openai"      — text-embedding-3-small, cần OPENAI_API_KEY, tính phí theo token
# "huggingface" — chạy local, miễn phí, cần tải model lần đầu (~1-2GB), chậm hơn trên CPU
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "huggingface")

OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# BAAI/bge-m3: model embedding đa ngôn ngữ, hỗ trợ tiếng Việt tốt, chạy local
# qua sentence-transformers — phương án nếu không muốn phụ thuộc OpenAI/chi phí.
HF_EMBED_MODEL = os.getenv("HF_EMBED_MODEL", "BAAI/bge-m3")


def embed_model_id() -> str:
    """Dấu vân tay model đang cấu hình, vd 'openai:text-embedding-3-small'. Được lưu vào
    metadata collection (vector_store.py) để phát hiện đổi model giữa chừng — kể cả khi
    dimension trùng nhau (khi đó Chroma không báo lỗi gì). Không cần API key."""
    name = HF_EMBED_MODEL if EMBED_PROVIDER == "huggingface" else OPENAI_EMBED_MODEL
    return f"{EMBED_PROVIDER}:{name}"


@lru_cache(maxsize=1)
def get_embed_model() -> BaseEmbedding:
    """Trả về ĐÚNG 1 instance (cache qua lru_cache) — tránh load lại model
    HuggingFace (tốn vài giây) hoặc mở nhiều client OpenAI mỗi lần gọi."""
    if EMBED_PROVIDER == "huggingface":
        # Import trong hàm (lazy import): không bắt buộc cài sentence-transformers
        # nếu người dùng chỉ chạy OpenAI — giảm dependency không cần thiết.
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        return HuggingFaceEmbedding(model_name=HF_EMBED_MODEL)

    if EMBED_PROVIDER == "openai":
        from llama_index.embeddings.openai import OpenAIEmbedding
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "EMBED_PROVIDER=openai nhưng thiếu OPENAI_API_KEY trong .env. "
                "Đặt API key, hoặc đổi EMBED_PROVIDER=huggingface để chạy local."
            )
        return OpenAIEmbedding(model=OPENAI_EMBED_MODEL, api_key=api_key)

    raise ValueError(f"EMBED_PROVIDER không hợp lệ: '{EMBED_PROVIDER}'")


def embed_dim() -> int:
    """Số chiều vector — khai báo tường minh để phát hiện SỚM nếu lỡ đổi
    model giữa chừng mà collection cũ đã lưu vector ở dimension khác (Chroma
    sẽ raise lỗi rõ ràng ngay, thay vì âm thầm trả kết quả sai)."""
    return {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "BAAI/bge-m3": 1024,
    }.get(OPENAI_EMBED_MODEL if EMBED_PROVIDER == "openai" else HF_EMBED_MODEL, 1536)