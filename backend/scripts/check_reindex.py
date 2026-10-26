"""Kiểm tra hành vi nạp lại và cập nhật tăng dần, kể cả khi lỗi giữa chừng.

Chạy hoàn toàn trên một kho Chroma TẠM với embedding giả xác định: không gọi API, không đụng index
của ứng dụng. Mã nguồn được kiểm tra là IndexManager thật (ingest_nodes, incremental_reindex_nodes).

Mỗi kịch bản ghi lại: mong đợi, kết quả thực tế, và "đạt / lỗi đã biết" – lỗi đã biết là hành vi có thật
của mã hiện tại cần nêu trong báo cáo (vd. rollback xóa cả bản tốt trước đó).

Chạy tại thư mục backend:
  python -m scripts.check_reindex
  python -m scripts.check_reindex --from-file-id <file_id>   # dùng văn bản Node thật (chỉ đọc) thay cho văn bản mẫu
"""
import argparse
import hashlib
import math
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import chromadb
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.schema import TextNode

from scripts.run_utils import BACKEND_DIR, append_jsonl, git_info, new_run_dir, write_json

FILE_ID = "f" * 32
FILE_NAME = "kiem_thu.pdf"


class FakeEmbed(BaseEmbedding):
    """Vector 16 chiều suy từ SHA-256 của văn bản: cùng văn bản cho cùng vector, không cần mạng."""

    def _vec(self, text: str) -> list[float]:
        raw = hashlib.sha256(text.encode()).digest()
        v = [b / 255 - 0.5 for b in raw[:16]]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def _get_query_embedding(self, query): return self._vec(query)
    async def _aget_query_embedding(self, query): return self._vec(query)
    def _get_text_embedding(self, text): return self._vec(text)


def sample_pages(from_file_id: str | None, source_dir: str) -> list[tuple[int, str]]:
    """(trang, văn bản) của các Node đầu vào. Văn bản mẫu cố ý có một đoạn lặp ở hai trang khác nhau."""
    if from_file_id:
        col = chromadb.PersistentClient(path=source_dir).get_collection("SGKCTST")
        got = col.get(where={"file_id": from_file_id}, include=["documents", "metadatas"])
        rows = sorted(zip(got["metadatas"], got["documents"]), key=lambda r: r[0].get("chunk_index", 0))
        return [(int(m["page"]), d) for m, d in rows]
    pages = [(p, f"Trang {p}: nội dung kiến thức số {p} về hàm số và đạo hàm.") for p in range(1, 13)]
    pages += [(3, "Bài tập luyện tập"), (9, "Bài tập luyện tập")]          # cùng chữ, khác trang
    return pages


def make_nodes(pages) -> list[TextNode]:
    return [TextNode(text=text, metadata={"file_id": FILE_ID, "file_name": FILE_NAME, "page": page,
                                          "chunk_index": i}) for i, (page, text) in enumerate(pages)]


def state(collection) -> dict:
    got = collection.get(where={"file_id": FILE_ID}, include=["documents", "metadatas"])
    return {"count": len(got["ids"]), "ids": sorted(got["ids"]),
            "pages_of": {d: sorted(m["page"] for m, dd in zip(got["metadatas"], got["documents"]) if dd == d)
                         for d in set(got["documents"])}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-file-id", default=None)
    parser.add_argument("--source-chroma-dir", default=str(BACKEND_DIR / "storage" / "chroma_db"),
                        help="kho để đọc văn bản Node thật (chỉ đọc)")
    args = parser.parse_args()

    import app.rag.indexing.index_manager as im
    import app.rag.indexing.vector_store as vs

    tmp = Path(tempfile.mkdtemp(prefix="check_reindex_"))
    collection = chromadb.PersistentClient(path=str(tmp)).get_or_create_collection(
        "check_reindex", metadata={"hnsw:space": "cosine"})
    vs.get_collection = lambda: collection           # IndexManager và vector_store dùng kho tạm
    im.get_embed_model = lambda: FakeEmbed()
    im.INGEST_BATCH_SIZE = 4                          # lô nhỏ để giả lập lỗi "giữa batch"

    run_dir = new_run_dir("check_reindex")
    raw = run_dir / "raw.jsonl"
    results = []
    pages = sample_pages(args.from_file_id, args.source_chroma_dir)
    n = len(pages)
    dup_texts = [t for t, c in Counter(t for _, t in pages).items() if c > 1]

    def record(name, expected, actual, ok, known_issue=False, note=""):
        verdict = "đạt" if ok else ("lỗi đã biết" if known_issue else "KHÔNG ĐẠT")
        row = {"scenario": name, "expected": expected, "actual": actual, "verdict": verdict, "note": note}
        results.append(row)
        append_jsonl(raw, row)
        print(f"[{verdict:^11}] {name}\n    mong đợi: {expected}\n    thực tế : {actual}" + (f"\n    ghi chú : {note}" if note else ""))

    def fresh_manager():
        mgr = im.IndexManager()
        mgr._ensure_ready()
        return mgr

    def fail_on_call(mgr, call_no):
        """Cho insert_nodes ném lỗi ở lần gọi thứ call_no – giả lập API embedding lỗi giữa chừng."""
        original, counter = mgr._index.insert_nodes, {"n": 0}

        def insert(nodes, **kw):
            counter["n"] += 1
            if counter["n"] == call_no:
                raise RuntimeError("Lỗi giả lập: API embedding không phản hồi")
            return original(nodes, **kw)
        mgr._index.insert_nodes = insert

    # 1. Nạp lần đầu
    mgr = fresh_manager()
    mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    s1 = state(collection)
    record("1. Nạp lần đầu", f"{n} Node, mã dạng <file_id>_00000",
           f"{s1['count']} Node, mã đầu {s1['ids'][0][-6:]}", s1["count"] == n)

    # 2. Nạp lại y hệt: ID tất định nên ghi đè, không nhân bản
    mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    s2 = state(collection)
    record("2. Nạp lại cùng tài liệu (ingest_nodes)", f"vẫn {n} Node, cùng tập mã",
           f"{s2['count']} Node, tập mã {'giống' if s2['ids'] == s1['ids'] else 'khác'}",
           s2["count"] == n and s2["ids"] == s1["ids"])

    # 3. Cập nhật tăng dần, nội dung không đổi
    stats = mgr.incremental_reindex_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    s3 = state(collection)
    expected_kept = n - sum(Counter(t for _, t in pages)[t] - 1 for t in dup_texts)
    record("3. Reindex tăng dần, nội dung không đổi", f"embedded 0, deleted 0, giữ {n} Node",
           f"{stats}, còn {s3['count']} Node", stats["embedded_chunks"] == 0 and s3["count"] == n,
           known_issue=bool(dup_texts),
           note=(f"{len(dup_texts)} đoạn trùng chữ ở nhiều trang bị gộp về một mã (MD5 → một ID): còn "
                 f"{s3['count']}/{n} Node, mong đợi theo mã hiện tại {expected_kept}" if dup_texts else ""))
    if dup_texts:
        t = dup_texts[0]
        record("3b. Đoạn trùng chữ ở hai trang sau reindex", f"giữ Node ở cả các trang {sorted(p for p, x in pages if x == t)}",
               f"chỉ còn trang {s3['pages_of'].get(t)}", False, known_issue=True,
               note="Trích dẫn cho đoạn này chỉ còn trỏ về một trang.")

    # Đưa kho về trạng thái sạch cho các kịch bản sau
    mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)

    # 4. Cập nhật tăng dần: sửa 2 đoạn, thêm 1, bỏ 1
    changed = list(pages)
    changed[0] = (changed[0][0], changed[0][1] + " (đã sửa)")
    changed[1] = (changed[1][0], changed[1][1] + " (đã sửa)")
    removed = changed.pop(2)
    changed.append((99, "Đoạn mới thêm ở trang 99."))
    stats = mgr.incremental_reindex_nodes(make_nodes(changed), FILE_ID, FILE_NAME)
    s4 = state(collection)
    record("4. Reindex tăng dần: sửa 2, thêm 1, bỏ 1", "embedded 3, deleted 3",
           f"{stats}, còn {s4['count']} Node",
           stats["embedded_chunks"] == 3 and stats["deleted_chunks"] == 3, known_issue=bool(dup_texts),
           note=f"Đoạn bị bỏ: trang {removed[0]}." + (" Số liệu lệch do đoạn trùng chữ." if dup_texts else ""))

    # 5. Lỗi giữa batch khi NẠP LẠI tài liệu đã có (ingest_nodes xóa bản cũ trước khi ghi)
    mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    before = state(collection)["count"]
    fail_on_call(mgr, 2)
    error = None
    try:
        mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    except Exception as exc:
        error = type(exc).__name__
    s5 = state(collection)
    record("5. Lỗi ở lô thứ 2 khi nạp lại tài liệu đã có", f"báo lỗi và GIỮ NGUYÊN {before} Node của bản cũ",
           f"lỗi {error}; còn {s5['count']} Node", s5["count"] == before, known_issue=True,
           note="ingest_nodes xóa Node cũ trước khi ghi; rollback xóa luôn phần vừa ghi → tài liệu mất khỏi index.")

    # 6. Lỗi giữa chừng khi cập nhật tăng dần
    mgr = fresh_manager()
    mgr.ingest_nodes(make_nodes(pages), FILE_ID, FILE_NAME)
    before = state(collection)["count"]
    fail_on_call(mgr, 1)
    error = None
    try:
        mgr.incremental_reindex_nodes(make_nodes(changed), FILE_ID, FILE_NAME)
    except Exception as exc:
        error = type(exc).__name__
    s6 = state(collection)
    record("6. Lỗi khi embedding đoạn mới trong reindex tăng dần",
           f"báo lỗi và giữ nguyên {before} Node cũ",
           f"lỗi {error}; còn {s6['count']} Node", s6["count"] == before, known_issue=True,
           note="Các Node cũ cần xóa đã bị xóa trước bước embedding; rollback chỉ dọn Node mới, không khôi phục Node cũ.")

    # 7. Khóa ghi chỉ trong một tiến trình
    record("7. Khóa ghi giữa nhiều tiến trình", "chạy một worker uvicorn",
           "_write_lock là threading.Lock: chỉ có tác dụng trong một tiến trình", True,
           note="Không kiểm thử nhiều worker; hệ thống hỗ trợ đúng 1 worker.")

    summary = Counter(r["verdict"] for r in results)
    write_json(run_dir / "manifest.json", {
        "kind": "check_reindex", "store": "Chroma tạm + embedding giả (không gọi API)",
        "input": {"source": args.from_file_id or "văn bản mẫu", "nodes": n, "duplicate_texts": len(dup_texts)},
        "batch_size_for_test": im.INGEST_BATCH_SIZE, "results": results, "summary": dict(summary),
        "git": git_info()})
    lines = [f"# Kiểm tra reindex ({n} Node đầu vào, {len(dup_texts)} đoạn trùng chữ)", "",
             "| Kịch bản | Mong đợi | Thực tế | Kết luận |", "|---|---|---|---|"]
    lines += [f"| {r['scenario']} | {r['expected']} | {r['actual']} | {r['verdict']} |" for r in results]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nTổng: {dict(summary)}\nĐã lưu: {run_dir}")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
