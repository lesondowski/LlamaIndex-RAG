import os
import math
import time
import json
from pathlib import Path
import asyncio
from dotenv import load_dotenv

from google import genai
from ragas.llms import llm_factory, LangchainLLMWrapper
from ragas import SingleTurnSample
from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall
from app.rag.retrieval.retriever import Retriever

# Nạp cấu hình trước khi import retriever.
ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")
TOP_K = 5
FILE_ID = "1"

DATASET = [
    {
        "question": "Nội năng của một vật là gì?",
        "file_ids": [FILE_ID],
        "reference": (
            "Nội năng của một vật là tổng động năng "
            "và thế năng tương tác của các phân tử cấu tạo nên vật."
        ),
    },
    {
        "question": "Có những cách nào làm thay đổi nội năng của vật?",
        "file_ids": [FILE_ID],
        "reference": (
            "Có hai cách làm thay đổi nội năng của vật: "
            "thực hiện công và truyền nhiệt."
        ),
    },
]


def _key_validate(api_key= "GOOGLE_API_KEY", model_ragas="RAGAS_JUDGE_MODEL", file_id="1"):
    api_key = os.getenv(api_key, "").strip()
    model_name = os.getenv(model_ragas, "").strip()

    client = genai.Client(api_key=api_key)
    judge = llm_factory(model=model_name, provider="google", client=client)

    if not api_key:
        raise ValueError("Thiếu GOOGLE_API_KEY.")

    if not model_name:
        raise ValueError("Thiếu RAGAS_JUDGE_MODEL.")
    if any(
        file_id == "1"
        for item in DATASET
        for file_id in item["file_ids"]
    ):
        raise ValueError("Hãy thêm tham số FILE_ID bằng ID thật trước khi chạy, ID mặc định đang không khớp.")

    return model_name, judge   
    
def _save_output(root: str):
    run_id = time.strftime("%Y%m%d-%H%M%S") # YYYY.MM.DD - HH.MM.SS
    output = (
        ROOT / "backend" / "evaluation" / "runs"
        / f"{run_id}_ragas_retrieval.jsonl"
    )
    return output.parent.mkdir(parents=True, exist_ok=True)




async def main():
    
    model_name, judge = _key_validate() # kiểm tra vaildate của các key api


    precision_metric = LLMContextPrecisionWithReference(llm=judge)
    recall_metric = LLMContextRecall(llm=judge)

    retriever = Retriever()
    rows = []
    
    output = _save_output()

    with output.open("w", encoding="utf-8") as stream:
        for index, item in enumerate(DATASET, start=1):
            print(f"\n[{index}/{len(DATASET)}] {item['question']}")

            row = {
                **item,
                "top_k": TOP_K,
                "judge_model": model_name,
            }

            try:
                # Đo riêng retrieval, không gồm thời gian giám khảo.
                started = time.perf_counter()
                results = await asyncio.to_thread(
                    retriever.retrieve,
                    question=item["question"],
                    top_k=TOP_K,
                    file_ids=item["file_ids"],
                )

                row["retrieval_seconds"] = time.perf_counter() - started
                row["node_ids"] = [r.node.node_id for r in results]
                row["pages"] = [r.node.metadata.get("page") for r in results]
                row["retrieved_contexts"] = [r.node.text for r in results]

                if not results:
                    # Quy ước của bài đánh giá: câu có đáp án nhưng
                    # không lấy được context thì chấm 0.
                    row["context_precision"] = 0.0
                    row["context_recall"] = 0.0
                    row["status"] = "empty_retrieval"
                else:
                    sample = SingleTurnSample(
                        user_input=item["question"],
                        retrieved_contexts=row["retrieved_contexts"],
                        reference=item["reference"],
                    )

                    metrics = {
                        "context_precision": precision_metric,
                        "context_recall": recall_metric,
                    }

                    for name, metric in metrics.items():
                        score = float(
                            await metric.single_turn_ascore(sample)
                        )
                        if not math.isfinite(score):
                            raise ValueError(f"{name} trả điểm không hợp lệ.")
                        row[name] = score

                    row["status"] = "ok"

                print("Precision:", row["context_precision"])
                print("Recall:   ", row["context_recall"])

            except Exception as exc:
                # Lỗi API/chấm điểm không được coi là điểm 0.
                row["status"] = "error"
                row["error_type"] = type(exc).__name__
                print("Lỗi:", row["error_type"])

            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()

    # Chỉ tổng hợp những câu có đủ hai điểm.
    valid = [
        row for row in rows
        if row["status"] in {"ok", "empty_retrieval"}
    ]

    print(f"\nSố câu có kết quả: {len(valid)}/{len(rows)}")
    print(f"Số câu lỗi: {len(rows) - len(valid)}")

    if valid:
        for name in ("context_precision", "context_recall"):
            average = sum(row[name] for row in valid) / len(valid)
            print(f"{name}@{TOP_K}: {average:.3f}")

    print("Kết quả:", output)


if __name__ == "__main__":
    asyncio.run(main())