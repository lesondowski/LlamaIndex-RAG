import os
import json
from pathlib import Path
from time import perf_counter
from dotenv import load_dotenv
from app.rag.indexing.index_manager import get_index_manager
from app.rag.indexing.embeddings import get_embed_model

backend_dir = Path(__file__).resolve().parents[2]
project_dir = backend_dir.parent

def main() -> None:
    load_dotenv(project_dir / ".env")

    nodes_path = (
        backend_dir
        / "app"
        / "database"
        / "data"
        / "nodes"
        / "nodes_1.json"
    )

    if not nodes_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file nodes: {nodes_path}")

    nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
    texts = [
        item["text"].strip()
        for item in nodes
        if item.get("text", "").strip()
    ][:3]

    if not texts:
        raise RuntimeError("JSON không có node nào chứa text.")

    embed_model = get_embed_model()
    vectors = embed_model.get_text_embedding_batch(texts)

    print(f"Số chunk đã thử: {len(vectors)}")
    print(f"Số chiều vector: {len(vectors[0])}")
    print("Embedding Gemini hoạt động.")



if __name__ == "__main__":
    main()