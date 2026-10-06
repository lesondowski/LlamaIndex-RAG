"""
Cấu hình và khởi tạo embedding model cho pipeline RAG.

Cung cấp một instance embedding cho mỗi Python process, dùng chung cho cả
ingestion và retrieval. Nếu hai giai đoạn dùng cấu hình không tương thích, hệ
thống có thể gặp dimension mismatch hoặc vẫn chạy nhưng chất lượng retrieval
giảm.

Embedding được định danh bởi bộ ba (provider, model, dimension). Định danh này
được vector_store.py lưu vào metadata collection của Chroma và kiểm tra mỗi lần
mở; nếu cấu hình đổi sau khi index, ``EmbeddingModelMismatchError`` được raise.

Phạm vi: mỗi Python process có instance riêng. Với ``uvicorn --workers N``,
mọi worker phải được khởi chạy với cùng cấu hình embedding.
"""

import logging
import os
import threading
from dataclasses import dataclass

from google.genai.types import EmbedContentConfig, HttpOptions
from llama_index.core.embeddings import BaseEmbedding
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

log = logging.getLogger(__name__)

DEFAULT_EMBED_PROVIDER = "gemini"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"
DEFAULT_EMBED_DIMENSION = 3072

# SDK google-genai không đặt timeout mặc định (httpx nhận timeout=None), nên
# một kết nối treo giữ luồng ingest vô thời hạn. Tham số `timeout` của
# GoogleGenAIEmbedding không được forward xuống HTTP client, phải đặt qua
# HttpOptions (đơn vị mili-giây).
DEFAULT_EMBED_TIMEOUT_SECONDS = 60

# Ràng buộc của ứng dụng, không phải tuyên bố về hành vi cố định của SDK.
GEMINI_ALLOWED_DIMS = frozenset({768, 1536, 3072})

_ALLOWED_DIMS_BY_MODEL: dict[str, frozenset[int]] = {
    "gemini-embedding-001": GEMINI_ALLOWED_DIMS,
}


class EmbeddingError(RuntimeError):
    """Lỗi chung của tầng embedding."""


class EmbeddingConfigError(EmbeddingError):
    """Cấu hình embedding không hợp lệ."""


@dataclass(frozen=True)
class EmbeddingIdentity:
    """Danh tính bất biến của một cấu hình embedding."""

    provider: str
    model: str
    dimension: int

    def as_string(self) -> str:
        """Trả về định danh dạng 'provider:model:dimension'."""
        return f"{self.provider}:{self.model}:{self.dimension}"


# Cache cả model và identity dùng để tạo model. Lý do cache identity: nếu env
# bị đổi runtime sau khi model đã khởi tạo, embed_model_id() đọc lại env sẽ
# lệch với instance đang chạy. Cache đảm bảo định danh luôn phản ánh đúng cấu
# hình thực tế của model.
_model: BaseEmbedding | None = None
_identity: EmbeddingIdentity | None = None
_model_lock = threading.Lock()


def _get_provider() -> str:
    return os.getenv("EMBED_PROVIDER", DEFAULT_EMBED_PROVIDER).strip().lower()


def _get_gemini_model() -> str:
    return os.getenv("GEMINI_EMBED_MODEL", DEFAULT_EMBED_MODEL).strip()


def _get_gemini_dimension() -> int:
    raw = os.getenv("GEMINI_EMBED_DIM", str(DEFAULT_EMBED_DIMENSION)).strip()
    try:
        return int(raw)
    except ValueError:
        raise EmbeddingConfigError(
            f"GEMINI_EMBED_DIM='{raw}' phải là số nguyên."
        ) from None


def _get_timeout_seconds() -> int:
    raw = os.getenv("GEMINI_EMBED_TIMEOUT", str(DEFAULT_EMBED_TIMEOUT_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise EmbeddingConfigError(
            f"GEMINI_EMBED_TIMEOUT='{raw}' phải là số nguyên (giây)."
        ) from None
    if value < 1:
        raise EmbeddingConfigError(f"GEMINI_EMBED_TIMEOUT={value} không hợp lệ: phải >= 1.")
    return value


def _get_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingConfigError(
            "Thiếu GOOGLE_API_KEY. Lấy khóa tại "
            "https://aistudio.google.com/apikey và đặt vào backend/.env "
            "(không hardcode, không commit)."
        )
    return api_key


def _validate_dimension(model: str, dimension: int) -> None:
    """Bắt buộc mọi model dùng trong hệ thống phải có ràng buộc dimension
    tường minh — từ chối model chưa được khai báo trong registry."""
    allowed = _ALLOWED_DIMS_BY_MODEL.get(model)
    if allowed is None:
        raise EmbeddingConfigError(
            f"Model '{model}' chưa được khai báo trong _ALLOWED_DIMS_BY_MODEL. "
            f"Thêm vào registry trước khi dùng. "
            f"Model đã biết: {sorted(_ALLOWED_DIMS_BY_MODEL)}."
        )
    if dimension not in allowed:
        raise EmbeddingConfigError(
            f"dimension={dimension} không hợp lệ cho gemini:{model}. "
            f"Chỉ nhận: {sorted(allowed)}."
        )


def get_embedding_identity() -> EmbeddingIdentity:
    """Đọc và validate cấu hình embedding từ environment.

    Không đọc API key — identity không phụ thuộc credential.
    """
    provider = _get_provider()
    if provider != "gemini":
        raise EmbeddingConfigError(
            f"EMBED_PROVIDER='{provider}' chưa được hỗ trợ. Chỉ hỗ trợ: 'gemini'."
        )

    model = _get_gemini_model()
    if not model:
        raise EmbeddingConfigError("GEMINI_EMBED_MODEL không được để trống.")

    dimension = _get_gemini_dimension()
    _validate_dimension(model, dimension)

    return EmbeddingIdentity(provider=provider, model=model, dimension=dimension)


def embed_model_id() -> str:
    """Trả về định danh đầy đủ của embedding đang được dùng.

    Giá trị có dạng 'provider:model:dimension', ví dụ:
    'gemini:gemini-embedding-001:3072'.

    Nếu model đã khởi tạo, trả về identity đã cache — đảm bảo định danh khớp
    với instance đang chạy, kể cả khi environment bị đổi giữa chừng. Nếu chưa
    khởi tạo, đọc cấu hình trực tiếp từ environment.
    """
    if _identity is not None:
        return _identity.as_string()
    return get_embedding_identity().as_string()


def _create_gemini_embedding(
    model_name: str,
    dimension: int,
    api_key: str,
    timeout_seconds: int,
) -> BaseEmbedding:
    log.info(
        "Khởi tạo embedding: provider=gemini, model=%s, dimension=%d, timeout=%ds",
        model_name, dimension, timeout_seconds,
    )
    try:
        return GoogleGenAIEmbedding(
            model_name=model_name,
            api_key=api_key,
            embedding_config=EmbedContentConfig(output_dimensionality=dimension),
            http_options=HttpOptions(timeout=timeout_seconds * 1000),
        )
    except Exception as exc:
        raise EmbeddingError(
            f"Không khởi tạo được Gemini embedding "
            f"({type(exc).__name__}: {exc}). "
            f"Kiểm tra GEMINI_EMBED_MODEL và GOOGLE_API_KEY."
        ) from exc


def get_embed_model() -> BaseEmbedding:
    """Trả về embedding model của process hiện tại (lazy, thread-safe)."""
    global _model, _identity
    if _model is not None:
        return _model

    with _model_lock:
        if _model is None:
            identity = get_embedding_identity()
            api_key = _get_api_key()
            timeout_seconds = _get_timeout_seconds()
            new_model = _create_gemini_embedding(
                model_name=identity.model,
                dimension=identity.dimension,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
            )
            # Gán _identity trước _model: reader ở fast path (không lock) kiểm
            # tra _model trước, nên khi thấy _model đã set thì _identity chắc
            # chắn đã set → embed_model_id() không bao giờ lệch instance.
            _identity = identity
            _model = new_model
    return _model


def _reset_for_tests() -> None:
    """Xóa model và identity đã cache. Chỉ dùng trong test."""
    global _model, _identity
    with _model_lock:
        _model = None
        _identity = None


__all__ = [
    "EmbeddingConfigError",
    "EmbeddingError",
    "EmbeddingIdentity",
    "embed_model_id",
    "get_embed_model",
    "get_embedding_identity",
]