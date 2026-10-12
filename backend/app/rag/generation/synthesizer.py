import os
from functools import lru_cache
from llama_index.core import get_response_synthesizer
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.llms.google_genai import GoogleGenAI

from app.rag.generation.prompt import GROUNDED_QA_TEMPLATE


@lru_cache(maxsize=2) # có thể chỉnh lên 5 - 6 để nhớ được nhiều hơn

def _create_gemini_llm(model_name: str, api_key: str):
    return GoogleGenAI(model=model_name, api_key=api_key, temperature=0.0)

def get_gemini_llm():
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_LLM_MODEL", "").strip()
    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY.")
    if not model_name:
        raise ValueError("GEMINI_LLM_MODEL không được để trống.")
    return _create_gemini_llm(model_name=model_name,api_key=api_key)


def build_query_engine(retriever, llm=None):
    response_synthesizer = get_response_synthesizer(
        llm=llm or get_gemini_llm(),
        response_mode=ResponseMode.COMPACT,
        streaming= True,
        text_qa_template=GROUNDED_QA_TEMPLATE
    )
    return RetrieverQueryEngine(retriever=retriever, response_synthesizer=response_synthesizer)



