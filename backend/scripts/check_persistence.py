"""Kiểm tra chỉ mục còn nguyên sau khi khởi động lại máy chủ.

Chụp trạng thái trước và sau khi khởi động lại, rồi so sánh:
  python -m scripts.check_persistence snapshot --label truoc
  (tắt máy chủ bằng Ctrl+C, bật lại)
  python -m scripts.check_persistence snapshot --label sau
  python -m scripts.check_persistence compare <thư mục snapshot trước> <thư mục snapshot sau>

Snapshot chỉ ĐỌC Chroma (không ghi). Tùy chọn:
  --api http://127.0.0.1:8000   ghi thêm /health và trạng thái từng tài liệu do máy chủ báo
  --query "câu hỏi"             ghi thêm top-5 mã Node của một câu hỏi (gọi API Jina một lần)
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from scripts.run_utils import (BACKEND_DIR, collection_summary, git_info, new_run_dir, use_store, write_json)

load_dotenv(BACKEND_DIR / ".env")


def fetch_json(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:          # máy chủ chưa chạy cũng là một kết quả cần ghi lại
        return {"error": f"{type(exc).__name__}: {exc}"}


def cmd_snapshot(args) -> None:
    store = use_store(args.chroma_dir, args.collection)
    from app.rag.indexing.vector_store import get_collection
    started = time.perf_counter()
    summary = collection_summary(get_collection())
    snap = {"kind": "persistence_snapshot", "label": args.label, "store": store,
            "open_and_scan_ms": round((time.perf_counter() - started) * 1000, 1),
            "collection": summary, "git": git_info()}
    if args.api:
        files = fetch_json(f"{args.api}/files")
        snap["api"] = {"health": fetch_json(f"{args.api}/health"),
                       "files": [{k: f.get(k) for k in ("file_id", "name", "status")}
                                 for f in (files.get("files", []) if isinstance(files, dict) else [])]
                       or files}
    if args.query:
        from app.rag.retrieval.retriever import Retriever
        results = Retriever().retrieve(question=args.query, top_k=5)
        snap["query"] = {"question": args.query,
                         "top5": [{"node_id": r.node.node_id, "score": round(float(r.score), 4),
                                   "page": r.node.metadata.get("page")} for r in results]}
    run_dir = new_run_dir(f"persist_{args.label}")
    write_json(run_dir / "snapshot.json", snap)
    print(f"[{args.label}] {summary['name']}: {summary['count']} vector, mở + quét {snap['open_and_scan_ms']} ms")
    for file_id, e in summary["files"].items():
        print(f"  {file_id} | {e['nodes']} Node | trang {e['page_min']}–{e['page_max']} | ids {e['ids_sha256'][:12]}")
    if "api" in snap:
        print(f"  API /health: {snap['api']['health']}")
        for f in snap["api"]["files"] if isinstance(snap["api"]["files"], list) else []:
            print(f"  API /files: {f['file_id']} | {f['status']} | {f['name']}")
    if "query" in snap:
        print(f"  Top-5 '{args.query}': {[x['node_id'][-5:] for x in snap['query']['top5']]}")
    print(f"Đã lưu: {run_dir}")


def cmd_compare(args) -> None:
    before = json.loads((Path(args.before) / "snapshot.json").read_text(encoding="utf-8"))
    after = json.loads((Path(args.after) / "snapshot.json").read_text(encoding="utf-8"))
    checks = []

    def check(name, a, b):
        checks.append({"check": name, "before": a, "after": b, "ok": a == b})

    cb, ca = before["collection"], after["collection"]
    check("Tổng số vector", cb["count"], ca["count"])
    check("Dấu vân tay mô hình", cb["metadata"].get("embed_model"), ca["metadata"].get("embed_model"))
    check("Danh sách file_id", sorted(cb["files"]), sorted(ca["files"]))
    for file_id in sorted(set(cb["files"]) | set(ca["files"])):
        fb, fa = cb["files"].get(file_id, {}), ca["files"].get(file_id, {})
        check(f"Số Node {file_id[:8]}", fb.get("nodes"), fa.get("nodes"))
        check(f"Tập mã Node {file_id[:8]}", fb.get("ids_sha256", "")[:16], fa.get("ids_sha256", "")[:16])
    if "query" in before and "query" in after:
        check("Top-5 mã Node của câu hỏi mẫu", [x["node_id"] for x in before["query"]["top5"]],
              [x["node_id"] for x in after["query"]["top5"]])
    if "api" in before and "api" in after:
        status = lambda s: {f["file_id"]: f["status"] for f in s["api"]["files"] if isinstance(f, dict)}  # noqa: E731
        check("Trạng thái tài liệu do API báo", status(before), status(after))

    run_dir = new_run_dir("persist_compare")
    passed = sum(c["ok"] for c in checks)
    write_json(run_dir / "compare.json", {"before": args.before, "after": args.after, "checks": checks,
                                          "passed": passed, "total": len(checks)})
    lines = [f"# So sánh trước và sau khởi động lại: {passed}/{len(checks)} giống nhau", "",
             "| Kiểm tra | Trước | Sau | Kết quả |", "|---|---|---|---|"]
    lines += [f"| {c['check']} | {c['before']} | {c['after']} | {'giống' if c['ok'] else 'KHÁC'} |" for c in checks]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nĐã lưu: {run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--label", required=True)
    snap.add_argument("--chroma-dir", default=None)
    snap.add_argument("--collection", default=None)
    snap.add_argument("--api", default=None)
    snap.add_argument("--query", default=None)
    snap.set_defaults(func=cmd_snapshot)
    cmp = sub.add_parser("compare")
    cmp.add_argument("before")
    cmp.add_argument("after")
    cmp.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
