import argparse
import json
import math
import time
from pathlib import Path
from dotenv import load_dotenv
from app.rag.retrieval.retriever import Retriever

# Chroma integration test, Đo top-k trên index hiện có

TOP_K_VALUES = (3, 5, 8)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[position]


def load_dataset(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"Tập đánh giá rỗng: {path}")

    for row in rows:
        if not row.get("question", "").strip():
            raise ValueError("Mỗi record cần có question.")
        if len(row.get("file_ids", [])) != 1:
            raise ValueError(
                "Mỗi câu hỏi của bài đánh giá 1.pdf cần đúng một file_id."
            )   
        if not row.get("relevant_pages"):
            raise ValueError(
                "Hãy gán nhãn relevant_pages trước khi chạy đánh giá."
            )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Đánh giá dense retrieval trên Chroma hiện có.")
    parser.add_argument("--limit", type=int, help="Chỉ chạy N câu đầu để kiểm tra nhanh.")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit phải lớn hơn 0")
    print("Đang nạp cấu hình và thư viện retrieval...", flush=True)
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[2]
    load_dotenv(repo_root / ".env")

    dataset_path = repo_root / "backend" / "evaluation" / "retrieval_pdf.jsonl"
    rows = load_dataset(dataset_path)
    if args.limit is not None:
        rows = rows[:args.limit]
    print(
        f"Dataset: {dataset_path}\n"
        f"Số câu: {len(rows)}; số lượt retrieval: {len(rows) * len(TOP_K_VALUES)}",
        flush=True,
    )
    retriever = Retriever()

    metrics = {
        top_k: {
            "hits": [],
            "recalls": [],
            "reciprocal_ranks": [],
            "latencies_ms": [],
        }
        for top_k in TOP_K_VALUES
    }

    for question_index, row in enumerate(rows, start=1):
        print(f"[{question_index}/{len(rows)}] {row['question']}", flush=True)
        relevant_pages = {int(page) for page in row["relevant_pages"]}

        for top_k in TOP_K_VALUES:
            print(f"  top_k={top_k}: đang truy hồi (gồm gọi API embedding)...", flush=True)
            started = time.perf_counter()
            results = retriever.retrieve(
                question=row["question"],
                top_k=top_k,
                file_ids=row["file_ids"],
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"  Nhận {len(results)} chunks sau {elapsed_ms / 1000:.2f}s", flush=True)

            ranked_pages = [
                result.node.metadata.get("page")
                for result in results
            ]
            ranked_pages = [
                int(page) for page in ranked_pages if page is not None
            ]
            retrieved_relevant = set(ranked_pages) & relevant_pages

            first_relevant_rank = next(
                (
                    rank
                    for rank, page in enumerate(ranked_pages, start=1)
                    if page in relevant_pages
                ),
                None,
            )

            bucket = metrics[top_k]
            bucket["hits"].append(float(bool(retrieved_relevant)))
            bucket["recalls"].append(
                len(retrieved_relevant) / len(relevant_pages)
            )
            bucket["reciprocal_ranks"].append(
                1.0 / first_relevant_rank if first_relevant_rank else 0.0
            )
            bucket["latencies_ms"].append(elapsed_ms)

    print(f"Evaluation questions: {len(rows)}")
    print("Higher hit@k, recall@k, MRR are better; latency is in milliseconds.")
    print("top_k | hit@k | recall@k | MRR | p50 ms | p95 ms")

    for top_k, bucket in metrics.items():
        count = len(bucket["hits"])
        print(
            f"{top_k:5d} | "
            f"{sum(bucket['hits']) / count:.3f} | "
            f"{sum(bucket['recalls']) / count:.3f} | "
            f"{sum(bucket['reciprocal_ranks']) / count:.3f} | "
            f"{percentile(bucket['latencies_ms'], 0.50):.1f} | "
            f"{percentile(bucket['latencies_ms'], 0.95):.1f}"
        )


if __name__ == "__main__":
    main()
