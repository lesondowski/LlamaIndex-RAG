"""Ingest lại nodes từ *_nodes.json vào Chroma — không cần LlamaParse."""
import json
import sys
from pathlib import Path

# Load .env TRƯỚC khi import bất kỳ module nào đọc os.getenv() lúc import.
# Đường dẫn giống main.py: parents[2] của file này = repo root.
#   scripts/reingest_from_nodes.py → parents[0]=scripts, [1]=backend, [2]=repo root
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from llama_index.core.schema import TextNode

from app.rag.indexing.index_manager import get_index_manager
from app.rag.types import PAGE


def reingest(nodes_json: Path, file_id: str, display_name: str, tags: dict) -> int:
    rows = json.loads(nodes_json.read_text(encoding="utf-8"))
    print(f"Đọc {len(rows)} node từ {nodes_json.name}")

    nodes: list[TextNode] = []
    skipped_empty = 0

    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            skipped_empty += 1
            continue
        meta = dict(row.get("metadata") or {})
        if meta.get(PAGE) is None:
            raise ValueError(f"Node {row.get('id')!r} thiếu page.")
        meta["document_id"] = file_id
        meta.pop("doc_id", None)
        meta.pop("source_path", None)
        meta.update(tags)
        nodes.append(TextNode(text=text, metadata=meta))

    if not nodes:
        raise ValueError("Không có node nào có text.")

    print(f"Sẽ ingest {len(nodes)} node (bỏ {skipped_empty} text rỗng)")
    n = get_index_manager().ingest_nodes(
        nodes, file_id=file_id, file_name=display_name,
    )
    print(f"✅ Đã index {n} chunk với file_id={file_id}")
    return n


def main() -> None:
    if len(sys.argv) < 3:
        print("Dùng: python -m scripts.reingest_from_nodes <nodes.json> <file_id> [display_name] [tag=value ...]")
        sys.exit(1)
    nodes_json = Path(sys.argv[1])
    file_id = sys.argv[2]
    display_name = sys.argv[3] if len(sys.argv) > 3 and "=" not in sys.argv[3] else f"{file_id}.pdf"
    if not nodes_json.is_file():
        raise FileNotFoundError(f"Không thấy: {nodes_json}")
    tags: dict = {}
    for kv in sys.argv[3:]:
        if "=" not in kv:
            continue
        key, _, val = kv.partition("=")
        tags[key] = int(val) if key in ("grade", "volume") else val
    reingest(nodes_json, file_id, display_name, tags)


if __name__ == "__main__":
    main()