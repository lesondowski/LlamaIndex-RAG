# Đánh giá truy hồi `c1400_o252`

- Index: `eval_c1400_o252` tại `C:\Users\ngocn\OneDrive\Documents\GitHub\LlamaIndex-RAG\backend\storage\eval_indexes\c1400_o252` (505 vector)
- Bộ câu hỏi: `dense_retrieval_pdf.jsonl` (34 câu, sha256 503eea186cb60bf5)
- Truy hồi một lần với k = 10; các k nhỏ hơn lấy từ tiền tố danh sách xếp hạng

| k | hit@k = số câu tìm thấy | recall@k | MRR@k |
|---|---|---|---|
| 3 | 0.676 (23/34) | 0.676 | 0.402 |
| 5 | 0.853 (29/34) | 0.853 | 0.446 |
| 8 | 0.882 (30/34) | 0.882 | 0.451 |
| 10 | 0.882 (30/34) | 0.882 | 0.451 |

Độ trễ một lần truy hồi: p50 443.4 ms, p95 1042.9 ms, câu đầu tiên 1042.9 ms (gồm khởi tạo).

Câu không tìm thấy trang đúng trong top-10: 4
- [10] Bài 1 trong sách Vật lí 12 có tên là gì? – trang đúng [7], top trang [3, 5, 4, 125, 33]
- [11] Bài 2 trong sách Vật lí 12 có tên là gì? – trang đúng [11], top trang [3, 5, 43, 55, 44]
- [12] Bài 3 trong sách Vật lí 12 có tên là gì? – trang đúng [16], top trang [3, 5, 44, 55, 33]
- [16] Các phân tử trong chất khí chuyển động như thế nào? – trang đúng [8], top trang [37, 36, 34, 36, 35]
