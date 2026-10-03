import os
from functools import lru_cache
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.core.embeddings import BaseEmbedding
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "huggingface")

OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
HF_EMBED_MODEL = os.getenv("HF_EMBED_MODEL", "BAAI/bge-m3")


def _embedding_config() -> tuple[str, str, str]:
    provider = os.getenv("EMBED_PROVIDER", "gemini").strip().lower()

    model_name = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

    api_key = os.getenv("GOOGLE_API_KEY", "").strip()


    return provider, model_name, api_key

def embed_model_id() -> str:

    provider, model_name, _ = _embedding_config()

    if provider != "gemini":
        raise ValueError("Provider không được hỗ trợ - chỉ hỗ trợ cho gemini")

    return f"{provider}:{model_name}"


@lru_cache(maxsize=2)


def _create_gemini_ebedding(model_name: str, api_key:str) -> BaseEmbedding:

    return GoogleGenAIEmbedding(model_name,api_key )



def get_embed_model() -> BaseEmbedding:
    provider, model_name, api_key = _embedding_config()
    if provider  != "gemini":
        raise ValueError("Provider không được hỗ trợ - chỉ hỗ trợ cho gemini")
    if not api_key:
        raise ValueError("Không có API KEY - Vui lòng setup API KEY")
    return _create_gemini_ebedding(model_name, api_key)

