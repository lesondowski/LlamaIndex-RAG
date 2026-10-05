import os
from functools import lru_cache

from llama_index.core.embeddings import BaseEmbedding
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding


def _embedding_config() -> tuple[str, str, str]:
    provider = os.getenv("EMBED_PROVIDER", "gemini").strip().lower()
    model_name = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001").strip()
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    return provider, model_name, api_key


def embed_model_id() -> str:
    provider, model_name, _ = _embedding_config()
    if provider != "gemini":
        raise ValueError(f"Embedding provider chưa được hỗ trợ: {provider}")
    return f"{provider}:{model_name}"


@lru_cache(maxsize=2)
def _create_gemini_embedding(model_name: str, api_key: str) -> BaseEmbedding:
    return GoogleGenAIEmbedding(model_name=model_name, api_key=api_key)


def get_embed_model() -> BaseEmbedding:
    provider, model_name, api_key = _embedding_config()

    if provider != "gemini":
        raise ValueError(f"Embedding provider chưa được hỗ trợ: {provider}")
    if not model_name:
        raise ValueError("GEMINI_EMBED_MODEL không được để trống.")
    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY trong environment.")

    return _create_gemini_embedding(model_name, api_key)