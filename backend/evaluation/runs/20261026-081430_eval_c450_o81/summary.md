# Đánh giá truy hồi `c450_o81`

- Index: `eval_c450_o81` tại `C:\Users\ngocn\OneDrive\Documents\GitHub\LlamaIndex-RAG\backend\storage\eval_indexes\c450_o81` (799 vector)
- Bộ câu hỏi: `dense_retrieval_pdf.jsonl` (34 câu, sha256 503eea186cb60bf5)
- Truy hồi một lần với k = 10; các k nhỏ hơn lấy từ tiền tố danh sách xếp hạng

| k | hit@k = số câu tìm thấy | recall@k | MRR@k |
|---|---|---|---|
| 3 | 0.647 (22/34) | 0.647 | 0.422 |
| 5 | 0.735 (25/34) | 0.735 | 0.444 |
| 8 | 0.824 (28/34) | 0.824 | 0.457 |
| 10 | 0.853 (29/34) | 0.853 | 0.460 |

Độ trễ một lần truy hồi: p50 463.9 ms, p95 787.9 ms, câu đầu tiên 1133.9 ms (gồm khởi tạo).

Câu không tìm thấy trang đúng trong top-10: 5
- [1] Sách giáo khoa Vật lí 12 do nhà xuất bản nào phát hành? – trang đúng [2], top trang [4, 3, 4, 4, 125]
- [10] Bài 1 trong sách Vật lí 12 có tên là gì? – trang đúng [7], top trang [3, 4, 4, 5, 4]
- [11] Bài 2 trong sách Vật lí 12 có tên là gì? – trang đúng [11], top trang [3, 4, 4, 43, 5]
- [12] Bài 3 trong sách Vật lí 12 có tên là gì? – trang đúng [16], top trang [4, 3, 55, 4, 5]
- [16] Các phân tử trong chất khí chuyển động như thế nào? – trang đúng [8], top trang [36, 37, 36, 34, 36]
