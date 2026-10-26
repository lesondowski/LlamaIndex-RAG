# Đánh giá truy hồi `c700_o64`

- Index: `eval_c700_o64` tại `C:\Users\ngocn\OneDrive\Documents\GitHub\LlamaIndex-RAG\backend\storage\eval_indexes\c700_o64` (561 vector)
- Bộ câu hỏi: `dense_retrieval_pdf.jsonl` (34 câu, sha256 503eea186cb60bf5)
- Truy hồi một lần với k = 10; các k nhỏ hơn lấy từ tiền tố danh sách xếp hạng

| k | hit@k = số câu tìm thấy | recall@k | MRR@k |
|---|---|---|---|
| 3 | 0.618 (21/34) | 0.618 | 0.382 |
| 5 | 0.765 (26/34) | 0.765 | 0.418 |
| 8 | 0.882 (30/34) | 0.882 | 0.436 |
| 10 | 0.882 (30/34) | 0.882 | 0.436 |

Độ trễ một lần truy hồi: p50 443.7 ms, p95 896.8 ms, câu đầu tiên 4605.7 ms (gồm khởi tạo).

Câu không tìm thấy trang đúng trong top-10: 4
- [10] Bài 1 trong sách Vật lí 12 có tên là gì? – trang đúng [7], top trang [3, 5, 4, 4, 125]
- [11] Bài 2 trong sách Vật lí 12 có tên là gì? – trang đúng [11], top trang [3, 43, 55, 5, 4]
- [12] Bài 3 trong sách Vật lí 12 có tên là gì? – trang đúng [16], top trang [3, 44, 55, 5, 33]
- [16] Các phân tử trong chất khí chuyển động như thế nào? – trang đúng [8], top trang [37, 36, 35, 34, 36]
