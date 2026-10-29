import asyncio
import logging
import math
import time

import httpx
from llama_index.core.embeddings import BaseEmbedding
from pydantic import Field, SecretStr

log = logging.getLogger(__name__)
ENDPOINT = "https://api.jina.ai/v1/embeddings"
RETRYABLE = {429, 502, 503, 504}


class JinaRetrievalEmbedding(BaseEmbedding):

    """
    sử để backup cho embedding của gemini
    
    """

    api_key: SecretStr = Field(exclude=True, repr=False)
    dimensions: int = Field(default=1024, ge=1, le=1024)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)

    def _decode(self, response, count, task):
        body = response.json()
        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError("Jina trả sai số lượng embedding.")

        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("Jina trả embedding không hợp lệ.")

        indices = [row.get("index") for row in rows]
        if any(type(index) is not int for index in indices):
            raise ValueError("Jina trả index không hợp lệ.")
        if sorted(indices) != list(range(count)):
            raise ValueError("Jina trả index thiếu hoặc trùng.")

        vectors = []
        for row in sorted(rows, key=lambda item: item["index"]):
            vector = row.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise ValueError("Jina trả vector sai số chiều.")
            if any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in vector
            ):
                raise ValueError("Jina trả vector có giá trị không hợp lệ.")
            if not any(vector):
                raise ValueError("Jina trả vector toàn số 0.")

            vectors.append([float(value) for value in vector])

        log.info(
            "jina_embedding model=%s task=%s inputs=%d usage=%s",
            self.model_name,
            task,
            count,
            body.get("usage"),
        )
        return vectors

    def _request(self, texts, task):
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Không embed nội dung rỗng.")
        if self.model_name != "jina-embeddings-v3":
            raise ValueError("Bản triển khai này chỉ hỗ trợ jina-embeddings-v3.")

        payload = {
            "model": self.model_name,
            "input": texts,
            "task": task,
            "dimensions": self.dimensions,
            "embedding_type": "float",
            "normalized": True,
            "late_chunking": False,
            "truncate": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                response = client.post(
                    ENDPOINT,
                    headers=headers,
                    json=payload,
                )
                if (
                    response.status_code in RETRYABLE
                    and attempt < self.max_retries
                ):
                    retry_after = response.headers.get("Retry-After")
                    if retry_after is None:
                        delay = float(2 ** attempt)
                    else:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            response.raise_for_status()

                    if not math.isfinite(delay) or not 0 <= delay <= 60:
                        response.raise_for_status()

                    time.sleep(delay)
                    continue

                response.raise_for_status()
                return self._decode(response, len(texts), task)

        raise RuntimeError("Jina request không hoàn thành.")

    def _get_query_embedding(self, query):
        return self._request([query], "retrieval.query")[0]

    def _get_text_embedding(self, text):
        return self._request([text], "retrieval.passage")[0]

    def _get_text_embeddings(self, texts):
        return self._request(texts, "retrieval.passage")

    async def _aget_query_embedding(self, query):
        return await asyncio.to_thread(self._get_query_embedding, query)

    async def _aget_text_embedding(self, text):
        return await asyncio.to_thread(self._get_text_embedding, text)

    async def _aget_text_embeddings(self, texts):
        return await asyncio.to_thread(self._get_text_embeddings, texts)