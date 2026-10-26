"""Lập chỉ mục lại một tài liệu của ứng dụng từ bộ nhớ đệm trang, không gọi lại LlamaParse.

Dùng khi đổi cấu hình chia Node (CHUNK_SIZE/CHUNK_OVERLAP) hoặc khi LlamaParse lỗi, mà PDF đã từng được đọc
bằng build_eval_index (kết quả nằm ở evaluation/artifacts/<sha16>_pages.json).

Chia Node bằng đúng hàm _build_nodes của luồng tải lên (cùng CHUNK_SIZE, cùng metadata, cùng nhãn môn/lớp
trong tệp JSON đi kèm), rồi ghi đè toàn bộ Node cũ của file_id bằng ingest_nodes. Có gọi API Jina để embedding.

NÊN sao lưu storage/chroma_db và TẮT máy chủ trước khi chạy (Chroma chỉ an toàn với một tiến trình ghi).

Chạy tại thư mục backend:
  python -m scripts.reindex_from_cache --file-id <file_id trong storage/uploads>
"""
import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from llama_index.core import Document

from scripts.run_utils import (BACKEND_DIR, collection_summary, environment_info, git_info, new_run_dir,
                               sha256_file, write_json)

load_dotenv(BACKEND_DIR / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--uploads", default=str(BACKEND_DIR / "storage" / "uploads"))
    parser.add_argument("--artifacts", default=str(BACKEND_DIR / "evaluation" / "artifacts"))
    args = parser.parse_args()

    uploads = Path(args.uploads)
    pdf = uploads / f"{args.file_id}.pdf"
    if not pdf.is_file():
        raise SystemExit(f"Không thấy {pdf}")
    sidecar = uploads / f"{args.file_id}.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8-sig")) if sidecar.is_file() else {}
    display_name = meta.get("name") or pdf.name
    sha = sha256_file(pdf)
    cache = Path(args.artifacts) / f"{sha[:16]}_pages.json"
    if not cache.is_file():
        raise SystemExit(f"Chưa có bộ nhớ đệm {cache.name}. Chạy build_eval_index cho tệp này trước "
                         "(lệnh đó gọi LlamaParse một lần và lưu kết quả).")
    pages = json.loads(cache.read_text(encoding="utf-8"))

    from app.rag.indexing.index_manager import get_index_manager
    from app.rag.indexing.vector_store import get_collection
    from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE
    from app.rag.types import DocumentTags
    from app.routers.upload import _build_nodes

    tags = DocumentTags.from_input(meta.get("subject"), meta.get("grade"), meta.get("series"), meta.get("volume"))
    documents = [Document(text=p["text"], metadata={"page": p["page"], "source": str(pdf)}) for p in pages]
    nodes = _build_nodes(documents, args.file_id, display_name, tags)

    collection = get_collection()
    before = collection_summary(collection)["files"].get(args.file_id, {}).get("nodes", 0)
    print(f"Tệp: {display_name} | sha256 {sha[:16]} | {len(pages)} trang từ {cache.name}")
    print(f"Chia Node {CHUNK_SIZE}/{CHUNK_OVERLAP}: {len(nodes)} Node | nhãn {tags.to_metadata()} | "
          f"index hiện có {before} Node của file này")
    started = time.perf_counter()
    written = get_index_manager().ingest_nodes(nodes, file_id=args.file_id, file_name=display_name)
    seconds = time.perf_counter() - started
    after = collection_summary(collection)
    print(f"Đã ghi {written} Node trong {seconds:.1f} s; tổng vector của collection: {after['count']}")

    run_dir = new_run_dir(f"reindex_cache_{args.file_id[:8]}")
    write_json(run_dir / "manifest.json", {
        "kind": "reindex_from_cache", "file_id": args.file_id, "file_name": display_name, "sha256": sha,
        "pages_cache": cache.name, "pages": len(pages), "chunking": {"chunk_size": CHUNK_SIZE,
                                                                       "chunk_overlap": CHUNK_OVERLAP},
        "tags": tags.to_metadata(), "nodes_before": before, "nodes_after": written,
        "ingest_seconds": round(seconds, 1), "collection_after": after,
        "run": {"git": git_info(), "environment": environment_info()}})
    print(f"Đã lưu: {run_dir}")


if __name__ == "__main__":
    main()
