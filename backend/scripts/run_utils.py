"""Tiện ích dùng chung cho các script minh chứng: thư mục chạy, thông tin git, chọn kho Chroma.

Mọi script ghi kết quả vào backend/evaluation/runs/<thời điểm>_<tên>/ gồm:
  manifest.json  – corpus, index, cấu hình, commit, phiên bản thư viện
  raw.jsonl      – kết quả thô từng bước / từng câu hỏi
  summary.md     – bảng tóm tắt để chép vào báo cáo
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime
from importlib import metadata as pkg_metadata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = BACKEND_DIR / "evaluation" / "runs"
PACKAGES = ("llama-index-core", "llama-index-embeddings-jinaai", "llama-index-vector-stores-chroma",
            "llama-index-llms-google-genai", "llama-index-readers-llama-parse", "chromadb")


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def new_run_dir(name: str) -> Path:
    run_dir = RUNS_DIR / f"{now_tag()}_{name}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_info() -> dict:
    """Commit đang chạy và trạng thái sạch/bẩn – để biết kết quả sinh ra từ mã nguồn nào."""
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=BACKEND_DIR, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    changed = [line for line in run("status", "--porcelain").splitlines() if line.strip()]
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(changed), "changed_files": changed[:30]}


def environment_info() -> dict:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = pkg_metadata.version(name)
        except pkg_metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": platform.python_version(), "os": platform.platform(), "packages": versions}


def use_store(chroma_dir: str | Path | None, collection: str | None) -> dict:
    """Trỏ ứng dụng sang kho Chroma khác (vd. index đánh giá riêng) mà không sửa mã nguồn.

    Phải gọi trước lần đầu dùng vector_store/index_manager, vì client và collection được cache.
    """
    from app.rag.indexing import vector_store as vs
    if chroma_dir:
        vs.CHROMA_DIR = Path(chroma_dir).resolve()
        vs.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    if collection:
        vs.COLLECTION_NAME = collection
    vs._get_client.cache_clear()
    vs.get_collection.cache_clear()
    return {"chroma_dir": str(vs.CHROMA_DIR), "collection": vs.COLLECTION_NAME}


def collection_summary(collection) -> dict:
    """Thống kê một collection Chroma: tổng vector, cấu hình HNSW, số Node theo từng file_id."""
    got = collection.get(include=["metadatas"])
    per_file: dict[str, dict] = {}
    for node_id, meta in zip(got["ids"], got["metadatas"]):
        meta = meta or {}
        entry = per_file.setdefault(meta.get("file_id", "<không có file_id>"),
                                    {"file_name": meta.get("file_name"), "nodes": 0, "pages": set(), "ids": []})
        entry["nodes"] += 1
        if meta.get("page") is not None:
            entry["pages"].add(int(meta["page"]))
        entry["ids"].append(node_id)
    files = {}
    for file_id, entry in sorted(per_file.items()):
        pages = sorted(entry["pages"])
        files[file_id] = {
            "file_name": entry["file_name"],
            "nodes": entry["nodes"],
            "page_min": pages[0] if pages else None,
            "page_max": pages[-1] if pages else None,
            "distinct_pages": len(pages),
            # Băm danh sách mã Node: hai lần chụp có cùng giá trị nghĩa là tập Node không đổi.
            "ids_sha256": hashlib.sha256("\n".join(sorted(entry["ids"])).encode()).hexdigest(),
        }
    return {
        "name": collection.name,
        "count": collection.count(),
        "metadata": dict(collection.metadata or {}),
        "hnsw": (collection.configuration_json or {}).get("hnsw"),
        "files": files,
    }


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def append_jsonl(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
