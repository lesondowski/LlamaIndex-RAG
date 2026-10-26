"""Dựng index đánh giá riêng cho MỘT cấu hình chia Node, không đụng vào index của ứng dụng.

Mỗi cấu hình có thư mục Chroma và collection riêng (storage/eval_indexes/c700_o64, collection eval_c700_o64),
nên vector của các cấu hình – hay của các mô hình embedding khác nhau – không bao giờ bị trộn.

PDF chỉ được gửi lên LlamaParse một lần: kết quả theo trang được lưu ở evaluation/artifacts/<sha16>_pages.json
và dùng lại cho mọi cấu hình, nên khác biệt giữa các index chỉ đến từ cách chia Node.

file_id của index đánh giá = 32 kí tự đầu SHA-256 của PDF: cùng tệp luôn cho cùng file_id.

Chạy tại thư mục backend:
  python -m scripts.build_eval_index --pdf <đường dẫn PDF> --chunk-size 700 --overlap 64
  python -m scripts.build_eval_index --pdf <...> --chunk-size 700 --overlap 64 --dry-run   # chỉ chia Node, không embedding
"""
import argparse
import json
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv
from llama_index.core import Document

from scripts.run_utils import (BACKEND_DIR, collection_summary, environment_info, git_info, new_run_dir,
                               sha256_file, use_store, write_json)

load_dotenv(BACKEND_DIR / ".env")
ARTIFACTS = BACKEND_DIR / "evaluation" / "artifacts"


def load_pages(pdf: Path, sha: str, file_id: str, reparse: bool) -> tuple[list[dict], str]:
    """Danh sách trang {text, page} của PDF: đọc từ bộ nhớ đệm, hoặc gọi LlamaParse rồi lưu lại."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    cache = ARTIFACTS / f"{sha[:16]}_pages.json"
    if cache.is_file() and not reparse:
        return json.loads(cache.read_text(encoding="utf-8")), f"cache {cache.name}"
    from app.rag.ingestion.loader import PDFLoader
    started = time.perf_counter()
    docs = PDFLoader().load_llama_parse(path_pdf=str(pdf), markdown_output=ARTIFACTS / f"{sha[:16]}.md",
                                        document_id=file_id)
    pages = [{"page": int(d.metadata["page"]), "text": d.text} for d in docs if d.text and d.text.strip()]
    cache.write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
    return pages, f"LlamaParse {time.perf_counter() - started:.1f} s → {cache.name}"


def build_nodes(pages: list[dict], pdf: Path, file_id: str, chunk_size: int, overlap: int):
    """Giống _build_nodes của luồng tải lên, nhưng nhận chunk_size/overlap tùy ý."""
    from app.rag.ingestion.chunker import Chunking
    docs = [Document(text=p["text"].strip(), metadata={
        "document_id": file_id, "file_id": file_id, "file_name": pdf.name, "source": pdf.name,
        "document_type": "pdf", "parser": "llamacloud", "page": p["page"]}) for p in pages]
    nodes = [n for n in Chunking(chunk_size=chunk_size, chunk_overlap=overlap).chunk(docs) if n.text.strip()]
    for index, node in enumerate(nodes):
        node.metadata["chunk_index"] = index
    return nodes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--chunk-size", type=int, required=True)
    parser.add_argument("--overlap", type=int, required=True)
    parser.add_argument("--out-root", default=str(BACKEND_DIR / "storage" / "eval_indexes"))
    parser.add_argument("--reparse", action="store_true", help="bỏ qua bộ nhớ đệm, gọi lại LlamaParse")
    parser.add_argument("--dry-run", action="store_true", help="chỉ chia Node và thống kê, không embedding")
    args = parser.parse_args()

    pdf = Path(args.pdf).resolve()
    sha = sha256_file(pdf)
    file_id = sha[:32]
    tag = f"c{args.chunk_size}_o{args.overlap}"
    pages, page_source = load_pages(pdf, sha, file_id, args.reparse)
    nodes = build_nodes(pages, pdf, file_id, args.chunk_size, args.overlap)
    lens = [len(n.text) for n in nodes]
    stats = {"pages": len(pages), "nodes": len(nodes), "chars_mean": round(statistics.mean(lens)),
             "chars_median": statistics.median(lens), "chars_min": min(lens), "chars_max": max(lens)}
    print(f"PDF: {pdf.name} | sha256 {sha[:16]} | file_id {file_id}")
    print(f"Trang: {page_source} | {stats}")
    if args.dry_run:
        print("--dry-run: dừng trước bước embedding.")
        return

    store = use_store(Path(args.out_root) / tag, f"eval_{tag}")
    from app.rag.indexing.index_manager import get_index_manager
    from app.rag.indexing.vector_store import get_collection
    started = time.perf_counter()
    get_index_manager().ingest_nodes(nodes, file_id=file_id, file_name=pdf.name)
    ingest_s = time.perf_counter() - started

    # Chroma không cho sửa metadata có hnsw:space sau khi tạo, nên cấu hình chia Node được ghi vào
    # tệp index_info.json nằm cạnh kho; index_manifest.py đọc tệp này.
    import pymupdf
    with pymupdf.open(str(pdf)) as doc:
        pdf_pages = doc.page_count
    write_json(Path(store["chroma_dir"]) / "index_info.json", {
        "collection": store["collection"], "chunk_size": args.chunk_size, "chunk_overlap": args.overlap,
        "pdf": pdf.name, "pdf_pages": pdf_pages, "sha256": sha, "file_id": file_id})
    summary = collection_summary(get_collection())
    run_dir = new_run_dir(f"index_{tag}")
    write_json(run_dir / "manifest.json", {
        "kind": "eval_index",
        "index": {**store, "vectors": summary["count"], "hnsw": summary["hnsw"],
                  "metadata": summary["metadata"], "files": summary["files"]},
        "corpus": {"pdf": pdf.name, "sha256": sha, "file_id": file_id, "page_source": page_source},
        "chunking": {"chunk_size": args.chunk_size, "chunk_overlap": args.overlap, **stats},
        "timing": {"ingest_seconds": round(ingest_s, 1)},
        "run": {"git": git_info(), "environment": environment_info()},
    })
    print(f"Đã ghi {summary['count']} vector vào {store['chroma_dir']} (collection {store['collection']}) "
          f"trong {ingest_s:.1f} s")
    print(f"Manifest: {run_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
