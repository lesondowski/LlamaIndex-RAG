import os
from functools import lru_cache

from llama_index.core.embeddings import BaseEmbedding


def _embedding_config():
    provider = os.getenv("EMBED_PROVIDER", "gemini").strip().lower()

    if provider == "gemini":
        model = os.getenv(
            "GEMINI_EMBED_MODEL", "gemini-embedding-001"
        ).strip()
        key = os.getenv("GOOGLE_API_KEY", "").strip()
    elif provider == "jina":
        model = os.getenv(
            "JINA_EMBED_MODEL", "jina-embeddings-v3"
        ).strip()
        key = os.getenv("JINA_API_KEY", "").strip()
        if model != "jina-embeddings-v3":
            raise ValueError(
                "Bản triển khai này chỉ hỗ trợ jina-embeddings-v3."
            )
    else:
        raise ValueError(f"Provider chưa được hỗ trợ: {provider}")

    if not model:
        raise ValueError("Tên embedding model không được để trống.")

    return provider, model, key


def _jina_options():
    dimensions = int(os.getenv("JINA_EMBED_DIMENSIONS", "1024"))
    batch_size = int(os.getenv("JINA_EMBED_BATCH_SIZE", "16"))

    if dimensions not in {32, 64, 128, 256, 512, 768, 1024}:
        raise ValueError("JINA_EMBED_DIMENSIONS không hợp lệ.")
    if not 1 <= batch_size <= 64:
        raise ValueError("JINA_EMBED_BATCH_SIZE phải từ 1 đến 64.")

    return dimensions, batch_size


def embed_model_id():
    provider, model, _ = _embedding_config()
    if provider == "jina":
        dimensions, _ = _jina_options()
        return f"jina:{model}:dim{dimensions}:retrieval-v1"

    return f"gemini:{model}"


@lru_cache(maxsize=4)
def _create_embedding(
    provider, model, key, dimensions, batch_size
) -> BaseEmbedding:
    if provider == "gemini":
        from llama_index.embeddings.google_genai import (
            GoogleGenAIEmbedding,
        )
        return GoogleGenAIEmbedding(
            model_name=model,
            api_key=key,
        )

    from app.rag.indexing.jina_embeddings import JinaRetrievalEmbedding

    return JinaRetrievalEmbedding(
        model_name=model,
        api_key=key,
        dimensions=dimensions,
        embed_batch_size=batch_size,
    )


def get_embed_model() -> BaseEmbedding:
    provider, model, key = _embedding_config()

    if not key:
        variable = (
            "JINA_API_KEY" if provider == "jina" else "GOOGLE_API_KEY"
        )
        raise ValueError(f"Thiếu {variable}.")

    dimensions, batch_size = (
        _jina_options() if provider == "jina" else (0, 0)
    )
    return _create_embedding(
        provider, model, key, dimensions, batch_size
    )
