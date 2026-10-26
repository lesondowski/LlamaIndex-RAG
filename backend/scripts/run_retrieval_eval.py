"""Chạy đánh giá truy hồi trên một index và lưu đủ raw log để người khác kiểm tra lại.

Khác evaluate_dense_retrieval.py ở ba điểm:
  - chọn được index (thư mục Chroma + collection), nên mỗi cấu hình chia Node đo trên index riêng;
  - mỗi câu hỏi chỉ truy hồi MỘT lần với k lớn nhất, rồi tính hit/recall/MRR cho từng k từ tiền tố
    của danh sách xếp hạng – các k được so sánh trên cùng một kết quả truy hồi;
  - ghi raw.jsonl (từng câu: trang, điểm, mã Node, thời gian) + manifest.json + summary.md.

Chỉ số tính theo TRANG: hit@k = có ít nhất một Node thuộc trang đúng trong k Node đầu;
recall@k = số trang đúng xuất hiện / số trang đúng (các Node cùng trang tính một lần);
MRR@k = 1 / hạng của Node đầu tiên thuộc trang đúng (0 nếu không có trong k).

Chạy tại thư mục backend:
  python -m scripts.run_retrieval_eval --chroma-dir storage/eval_indexes/c700_o64 --collection eval_c700_o64 \
      --file-id <file_id của index> --name c700_o64
"""
import argparse
import json
import math
import time
from pathlib import Path

from dotenv import load_dotenv

from scripts.run_utils import (BACKEND_DIR, append_jsonl, collection_summary, environment_info, git_info,
                               new_run_dir, sha256_file, use_store, write_json)

load_dotenv(BACKEND_DIR / ".env")
DEFAULT_DATASET = BACKEND_DIR / "evaluation" / "dense_retrieval_pdf.jsonl"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def load_dataset(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for i, row in enumerate(rows, 1):
        if not row.get("question", "").strip() or not row.get("relevant_pages"):
            raise ValueError(f"Dòng {i}: thiếu question hoặc relevant_pages.")
    return rows


def score_one(ranked_pages: list[int], relevant: set[int], k: int) -> dict:
    top = ranked_pages[:k]
    found = set(top) & relevant
    first = next((rank for rank, page in enumerate(top, 1) if page in relevant), None)
    return {"hit": float(bool(found)), "recall": len(found) / len(relevant),
            "rr": 1.0 / first if first else 0.0, "first_rank": first}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chroma-dir", default=None, help="mặc định: index của ứng dụng (storage/chroma_db)")
    parser.add_argument("--collection", default=None, help="mặc định: SGKCTST")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--file-id", default=None, help="ghi đè file_ids trong bộ câu hỏi bằng file_id của index này")
    parser.add_argument("--ks", default="3,5,8,10")
    parser.add_argument("--name", default="eval")
    args = parser.parse_args()

    ks = sorted({int(k) for k in args.ks.split(",")})
    store = use_store(args.chroma_dir, args.collection)
    from app.rag.indexing.vector_store import get_collection
    from app.rag.retrieval.retriever import Retriever

    dataset_path = Path(args.dataset).resolve()
    rows = load_dataset(dataset_path)
    collection = get_collection()          # dừng ngay nếu dấu vân tay mô hình không khớp
    summary = collection_summary(collection)
    run_dir = new_run_dir(f"eval_{args.name}")
    raw_path = run_dir / "raw.jsonl"
    retriever = Retriever()

    per_k = {k: {"hit": [], "recall": [], "rr": []} for k in ks}
    latencies, misses = [], []
    for qid, row in enumerate(rows, 1):
        file_ids = [args.file_id] if args.file_id else row["file_ids"]
        relevant = {int(p) for p in row["relevant_pages"]}
        started = time.perf_counter()
        results = retriever.retrieve(question=row["question"], top_k=max(ks), file_ids=file_ids)
        ms = (time.perf_counter() - started) * 1000
        latencies.append(ms)
        ranked = [{"rank": r, "page": int(n.node.metadata["page"]), "score": round(float(n.score), 4),
                   "node_id": n.node.node_id} for r, n in enumerate(results, 1)]
        pages = [x["page"] for x in ranked]
        scores = {k: score_one(pages, relevant, k) for k in ks}
        for k in ks:
            for key in ("hit", "recall", "rr"):
                per_k[k][key].append(scores[k][key])
        if not scores[max(ks)]["hit"]:
            misses.append({"id": qid, "question": row["question"], "relevant_pages": sorted(relevant),
                           "top_pages": pages})
        append_jsonl(raw_path, {"id": qid, "question": row["question"], "file_ids": file_ids,
                                "relevant_pages": sorted(relevant), "latency_ms": round(ms, 1),
                                "ranked": ranked, "scores": scores})
        print(f"[{qid:>2}/{len(rows)}] {ms:7.0f} ms | trang đúng {sorted(relevant)} | top {pages[:5]} | "
              f"hạng đầu đúng: {scores[max(ks)]['first_rank']}")

    n = len(rows)
    table = [{"k": k, "hit": sum(per_k[k]["hit"]) / n, "found": int(sum(per_k[k]["hit"])),
              "recall": sum(per_k[k]["recall"]) / n, "mrr": sum(per_k[k]["rr"]) / n} for k in ks]
    latency = {"p50_ms": round(percentile(latencies, 0.5), 1), "p95_ms": round(percentile(latencies, 0.95), 1),
               "min_ms": round(min(latencies), 1), "max_ms": round(max(latencies), 1),
               "first_query_ms": round(latencies[0], 1)}
    write_json(run_dir / "manifest.json", {
        "kind": "retrieval_eval",
        "index": {**store, "vectors": summary["count"], "metadata": summary["metadata"], "files": summary["files"]},
        "dataset": {"path": str(dataset_path), "sha256": sha256_file(dataset_path), "questions": n,
                    "file_id_override": args.file_id},
        "method": {"ks": ks, "retrieve_once_at_k": max(ks), "unit": "page",
                   "latency_includes": "embedding câu hỏi qua API Jina (mạng) + tìm kiếm Chroma; "
                                       "câu đầu tiên gồm cả khởi tạo mô hình (xem first_query_ms)"},
        "results": table, "latency": latency, "misses_at_max_k": misses,
        "run": {"git": git_info(), "environment": environment_info()},
    })
    lines = [f"# Đánh giá truy hồi `{args.name}`", "",
             f"- Index: `{store['collection']}` tại `{store['chroma_dir']}` ({summary['count']} vector)",
             f"- Bộ câu hỏi: `{dataset_path.name}` ({n} câu, sha256 {sha256_file(dataset_path)[:16]})",
             f"- Truy hồi một lần với k = {max(ks)}; các k nhỏ hơn lấy từ tiền tố danh sách xếp hạng", "",
             "| k | hit@k = số câu tìm thấy | recall@k | MRR@k |", "|---|---|---|---|"]
    lines += [f"| {r['k']} | {r['hit']:.3f} ({r['found']}/{n}) | {r['recall']:.3f} | {r['mrr']:.3f} |" for r in table]
    lines += ["", f"Độ trễ một lần truy hồi: p50 {latency['p50_ms']} ms, p95 {latency['p95_ms']} ms, "
                  f"câu đầu tiên {latency['first_query_ms']} ms (gồm khởi tạo).", "",
              f"Câu không tìm thấy trang đúng trong top-{max(ks)}: {len(misses)}"]
    lines += [f"- [{m['id']}] {m['question']} – trang đúng {m['relevant_pages']}, top trang {m['top_pages'][:5]}"
              for m in misses]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nĐã lưu: {run_dir}")


if __name__ == "__main__":
    main()
