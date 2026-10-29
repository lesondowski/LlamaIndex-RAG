import json
import os
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import chromadb
from dotenv import load_dotenv
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.vector_stores.chroma import ChromaVectorStore

from app.rag.indexing.embeddings import embed_model_id, get_embed_model


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    load_dotenv(repo_root / ".env")

    nodes_json = Path(
        os.getenv(
            "NODES_JSON",
            str(backend_dir / "storage" / "pipeline_runs" / "2_nodes.json"),
        )
    )
    question = os.getenv("SMOKE_QUERY", "Tóm tắt nội dung chính của tài liệu.")

    if not nodes_json.is_file():
        raise FileNotFoundError(f"Không tìm thấy nodes JSON: {nodes_json}")

    rows = json.loads(nodes_json.read_text(encoding="utf-8"))
    nodes = [
        TextNode(
            id_=row["id"],
            text=row["text"],
            metadata=row["metadata"],
        )
        for row in rows
        if row.get("text", "").strip()
    ]
    if not nodes:
        raise ValueError("Nodes JSON không có node văn bản.")

    for node in nodes:
        if node.metadata.get("page") is None:
            raise ValueError(f"Node thiếu page metadata: {node.node_id}")

    embed_model = get_embed_model()
    probe = embed_model.get_text_embedding("Kiểm tra Gemini embedding.")
    if not probe:
        raise RuntimeError("Gemini trả về embedding rỗng.")

    print("Embedding dimension:", len(probe))
    print("Nodes to index:", len(nodes))

    with TemporaryDirectory(prefix="rag_chroma_smoke_") as temp_dir:
        client = chromadb.PersistentClient(path=temp_dir)
        collection_name = f"smoke_{uuid.uuid4().hex}"

        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embed_model": embed_model_id(),
            },
        )

        file_id = f"smoke-{uuid.uuid4().hex}"
        for node in nodes:
            node.metadata["file_id"] = file_id

        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store
        )
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            storage_context=storage_context,
            embed_model=embed_model,
        )
        index.insert_nodes(nodes)

        indexed_count = collection.count()
        if indexed_count != len(nodes):
            raise AssertionError(
                f"Expected {len(nodes)} vectors, got {indexed_count}"
            )

        results = index.as_retriever(similarity_top_k=3).retrieve(question)
        if not results:
            raise AssertionError("Chroma không trả về kết quả truy vấn.")

        for result in results:
            metadata = result.node.metadata
            assert metadata.get("file_id") == file_id
            assert metadata.get("page") is not None
            print(
                f"score={result.score}, "
                f"page={metadata.get('page')}, "
                f"node_id={result.node.node_id}"
            )

        print("Chroma indexed count:", indexed_count)
        print("Retrieved results:", len(results))
        print("Smoke test passed; temporary Chroma data will be removed.")


if __name__ == "__main__":
    main()