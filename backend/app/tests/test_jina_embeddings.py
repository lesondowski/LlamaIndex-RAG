import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from app.rag.indexing.jina_embeddings import JinaRetrievalEmbedding


class JinaEmbeddingTests(unittest.TestCase):
    def test_http_contract_sync_async_and_batch(self):
        requests = []
        real_client = httpx.Client

        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            return httpx.Response(200, json={
                "data": [
                    {
                        "index": i,
                        "embedding": [float(i + 1)] * 1024,
                    }
                    for i in reversed(
                        range(len(payload["input"]))
                    )
                ],
                "usage": {"total_tokens": 10},
            })

        def client_factory(**kwargs):
            return real_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        model = JinaRetrievalEmbedding(
            model_name="jina-embeddings-v3",
            api_key="fake-key-for-test",
            dimensions=1024,
            embed_batch_size=16,
        )

        with patch(
            "app.rag.indexing.jina_embeddings.httpx.Client",
            side_effect=client_factory,
        ):
            self.assertEqual(
                len(model.get_query_embedding("Câu hỏi")), 1024
            )
            vectors = model.get_text_embedding_batch(
                ["Nội dung"] * 17
            )
            self.assertEqual(len(vectors), 17)
            self.assertEqual(vectors[15][0], 16.0)
            asyncio.run(
                model.aget_query_embedding("Câu hỏi async")
            )

        self.assertEqual(
            [r["task"] for r in requests],
            [
                "retrieval.query",
                "retrieval.passage",
                "retrieval.passage",
                "retrieval.query",
            ],
        )
        self.assertEqual(
            [len(r["input"]) for r in requests],
            [1, 16, 1, 1],
        )


if __name__ == "__main__":
    unittest.main()