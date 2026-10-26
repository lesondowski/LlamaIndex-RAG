"""Ghi manifest corpus – index – run cho một kho Chroma.

Manifest trả lời ba câu hỏi để người khác kiểm tra được kết quả:
  - Corpus: những tệp PDF nào (tên, số trang, SHA-256, file_id) nằm trong index?
  - Index: collection nào, thư mục nào, mô hình embedding nào, cấu hình chia Node nào, bao nhiêu Node?
  - Run: tạo từ commit nào, máy nào, phiên bản thư viện nào?

Chỉ đọc, không ghi gì vào Chroma. Chạy tại thư mục backend:
  python -m scripts.index_manifest                                  # index của ứng dụng
  python -m scripts.index_manifest --chroma-dir storage/eval_indexes/c700_o64 --collection eval_c700_o64
"""
import argparse
import json
from pathlib import Path

import chromadb
from dotenv import load_dotenv

from scripts.run_utils import (BACKEND_DIR, collection_summary, environment_info, git_info, new_run_dir,
                               sha256_file, write_json)

load_dotenv(BACKEND_DIR / ".env")


def pdf_info(uploads: Path, file_id: str) -> dict:
    """Số trang và mã băm của tệp gốc trong storage/uploads (nếu có)."""
    pdf = uploads / f"{file_id}.pdf"
    if not pdf.is_file():
        return {"pdf_found": False}
    import pymupdf
    with pymupdf.open(str(pdf)) as doc:
        pages = doc.page_count
    return {"pdf_found": True, "pdf_pages": pages, "pdf_mb": round(pdf.stat().st_size / 1e6, 1),
            "sha256": sha256_file(pdf)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chroma-dir", default=str(BACKEND_DIR / "storage" / "chroma_db"))
    parser.add_argument("--collection", default="SGKCTST")
    parser.add_argument("--uploads", default=str(BACKEND_DIR / "storage" / "uploads"))
    parser.add_argument("--name", default=None, help="tên ngắn cho thư mục kết quả")
    args = parser.parse_args()

    from app.rag.indexing.embeddings import embed_model_id
    from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE

    client = chromadb.PersistentClient(path=str(Path(args.chroma_dir).resolve()))
    collection = client.get_collection(args.collection)
    summary = collection_summary(collection)

    stored_model = summary["metadata"].get("embed_model")
    current_model = embed_model_id()
    # Index đánh giá ghi cấu hình chia Node vào index_info.json; index ứng dụng dùng hằng số trong chunker.py.
    info_path = Path(args.chroma_dir) / "index_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    chunk = {
        "chunk_size": info.get("chunk_size", CHUNK_SIZE),
        "chunk_overlap": info.get("chunk_overlap", CHUNK_OVERLAP),
        "source": "index_info.json" if info else "chunker.py (CHUNK_SIZE)",
    }
    uploads = Path(args.uploads)
    for file_id, entry in summary["files"].items():
        entry.update(pdf_info(uploads, file_id))
        if not entry["pdf_found"] and info.get("file_id") == file_id:
            # Index đánh giá: PDF không nằm trong uploads, lấy thông tin tệp từ index_info.json.
            entry.update({"pdf_found": True, "file_name": info["pdf"], "pdf_pages": info.get("pdf_pages"),
                          "sha256": info["sha256"]})
        if entry.get("pdf_pages"):
            entry["page_check"] = ("khớp" if entry["page_max"] == entry["pdf_pages"]
                                   else f"lệch: metadata tới trang {entry['page_max']}, PDF có {entry['pdf_pages']} trang")

    manifest = {
        "kind": "index_manifest",
        "index": {"chroma_dir": str(Path(args.chroma_dir).resolve()), "collection": summary["name"],
                  "vectors": summary["count"], "hnsw": summary["hnsw"],
                  "embed_model_stored": stored_model, "embed_model_current_config": current_model,
                  "embed_model_match": stored_model == current_model, "chunking": chunk},
        "corpus": summary["files"],
        "run": {"git": git_info(), "environment": environment_info()},
    }
    run_dir = new_run_dir(args.name or f"manifest_{summary['name']}")
    write_json(run_dir / "manifest.json", manifest)

    lines = [f"# Manifest index `{summary['name']}`", "",
             f"- Thư mục: `{manifest['index']['chroma_dir']}`",
             f"- Tổng vector: {summary['count']}",
             f"- Mô hình embedding đã lưu: `{stored_model}` (cấu hình hiện tại: `{current_model}`, "
             f"{'khớp' if manifest['index']['embed_model_match'] else 'KHÔNG KHỚP'})",
             f"- Chia Node: {chunk['chunk_size']}/{chunk['chunk_overlap']} token (nguồn: {chunk['source']})",
             f"- HNSW: {summary['hnsw']}",
             f"- Commit: `{manifest['run']['git']['commit'][:12]}`"
             f"{' (có thay đổi chưa commit)' if manifest['run']['git']['dirty'] else ''}", "",
             "| file_id | Tệp | Node | Trang metadata | Trang PDF | SHA-256 (16) | Kiểm tra trang |",
             "|---|---|---|---|---|---|---|"]
    for file_id, e in summary["files"].items():
        lines.append(f"| {file_id} | {e['file_name']} | {e['nodes']} | {e['page_min']}–{e['page_max']} | "
                     f"{e.get('pdf_pages', '–')} | {(e.get('sha256') or '–')[:16]} | {e.get('page_check', 'không có PDF')} |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nĐã lưu: {run_dir}")


if __name__ == "__main__":
    main()
