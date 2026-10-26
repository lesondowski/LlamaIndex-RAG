# Trợ lý học tập — Hỏi đáp Sách giáo khoa bằng RAG

Chatbot hỏi đáp dựa trên **sách giáo khoa dạng PDF**, xây dựng theo kiến trúc **RAG (Retrieval-Augmented Generation)** với **LlamaIndex**. Người dùng tải sách lên, đặt câu hỏi bằng tiếng Việt và nhận câu trả lời **bám sát nội dung sách**, kèm **trích dẫn số trang** để đối chiếu. Hệ thống hiểu được **câu hỏi nối tiếp** ("Còn chu vi của nó?") nhờ bộ nhớ hội thoại và kỹ thuật viết lại câu hỏi (condense question).

---

## Mục lục

- [Tính năng chính](#tính-năng-chính)
- [Kiến trúc hệ thống](#kiến-trúc-hệ-thống)
- [Công nghệ sử dụng](#công-nghệ-sử-dụng)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Cài đặt](#cài-đặt)
- [Cấu hình môi trường](#cấu-hình-môi-trường)
- [Chạy ứng dụng](#chạy-ứng-dụng)
- [Hướng dẫn sử dụng](#hướng-dẫn-sử-dụng)
- [Pipeline RAG](#pipeline-rag)
- [Bộ nhớ hội thoại và viết lại câu hỏi](#bộ-nhớ-hội-thoại-và-viết-lại-câu-hỏi)
- [API](#api)
- [Nạp tài liệu bằng dòng lệnh](#nạp-tài-liệu-bằng-dòng-lệnh)
- [Đánh giá](#đánh-giá)
- [Kiểm thử](#kiểm-thử)
- [Bảo mật](#bảo-mật)
- [Xử lý sự cố](#xử-lý-sự-cố)
- [Hạn chế và hướng phát triển](#hạn-chế-và-hướng-phát-triển)

---

## Tính năng chính

| Nhóm | Tính năng |
|---|---|
| Nạp tài liệu | Tải PDF qua giao diện (kéo thả); kiểm tra định dạng (magic bytes `%PDF-`, giới hạn 50 MB); parse bằng **LlamaParse** (PDF sang Markdown, giữ bảng và công thức), dự phòng **OCR Unstructured**; xử lý ngầm và theo dõi tiến độ |
| Chia đoạn | Chia hai tầng: theo **tiêu đề Markdown**, sau đó theo **câu** (700 token, overlap 64); mỗi đoạn thuộc đúng **một trang** |
| Lập chỉ mục | Embedding **Jina v3** (đa ngôn ngữ, 1024 chiều), lưu bền vào **ChromaDB** (HNSW, cosine); không embedding lại mỗi lần chạy |
| Cập nhật tăng dần | Re-index bằng mã băm MD5: chỉ embedding đoạn mới hoặc đã thay đổi; sửa nhãn (môn, lớp) không cần embedding lại |
| Truy hồi | Dense retrieval top-k; **lọc metadata trước khi tìm** theo sách, môn, lớp, bộ sách, khoảng trang |
| Sinh câu trả lời | **Gemini** với prompt *grounded*: chỉ dùng nội dung sách, thiếu thông tin thì nói rõ, chống prompt injection; **stream** từng phần qua SSE |
| Trích dẫn | Marker `[n]` bấm được, mở đúng đoạn sách kèm tên file và **số trang** |
| Hỏi tiếp | Bộ nhớ hội thoại (6 tin gần nhất) kết hợp **condense question**: viết lại câu hỏi nối tiếp thành câu tự đủ nghĩa trước khi tìm |
| Giao diện | Markdown, công thức **KaTeX**, bảng, danh sách nhiều cấp; nền sáng/tối; responsive cho desktop, tablet và điện thoại |

---

## Kiến trúc hệ thống

```
+------------------------- Trình duyệt (frontend/) --------------------------+
|  index.html, style.css, app.js (giao diện, lịch sử chat trong bộ nhớ tab)   |
|  api.js (fetch / XHR / đọc luồng SSE)                                       |
+----------------+--------------------------------------------+--------------+
                 | POST /upload, GET/DELETE /files            | POST /query/stream
                 v                                            v
+---------------------------- Backend FastAPI (backend/app/) ----------------------------+
|  routers/upload.py                          routers/query.py                           |
|   - kiểm tra PDF, lưu file và metadata       - resolve_scope (phạm vi, lọc metadata)    |
|   - xử lý ngầm:                              - condense_question (nếu có lịch sử)       |
|       loader (LlamaParse / Unstructured)     - Retriever -> Chroma (top-k)              |
|       -> Chunking (tiêu đề -> câu)           - build_evidence_nodes -> citations (SSE)  |
|       -> IndexManager -> Jina -> Chroma      - synthesizer (Gemini) -> token (SSE)      |
+----------------------------------------------------------------------------------------+
          |                                        |
          v                                        v
   backend/storage/uploads/                 backend/storage/chroma_db/
   (PDF và file .json metadata)             (vector, collection "SGKCTST")
```

---

## Công nghệ sử dụng

| Thành phần | Công nghệ |
|---|---|
| Framework RAG | LlamaIndex (`llama-index-core`) |
| Backend | FastAPI, Uvicorn, Pydantic v2 |
| Parse PDF | LlamaParse (LlamaCloud), Unstructured (OCR dự phòng), PyMuPDF (kiểm tra cấu trúc) |
| Embedding | Jina AI `jina-embeddings-v3` |
| Vector database | ChromaDB (persistent, HNSW, cosine) |
| LLM | Google Gemini (`llama-index-llms-google-genai`) |
| Frontend | HTML, CSS, JavaScript thuần (không framework), KaTeX |

---

## Cấu trúc thư mục

```
LlamaIndex-RAG/
├── backend/
│   ├── app/
│   │   ├── main.py                    # Khởi tạo FastAPI, CORS, logging, /health
│   │   ├── config.py                  # Thư mục upload, giới hạn dung lượng
│   │   ├── schemas.py                 # QueryRequest, ChatTurn (kiểm tra đầu vào)
│   │   ├── routers/
│   │   │   ├── upload.py              # /upload, /files, /files/{id}/status|reindex|metadata
│   │   │   └── query.py               # /query/stream (SSE)
│   │   ├── rag/
│   │   │   ├── types.py               # Khóa metadata, whitelist môn/lớp, DocumentTags, QueryScope
│   │   │   ├── pipeline.py            # Chạy pipeline từ dòng lệnh
│   │   │   ├── ingestion/
│   │   │   │   ├── loader.py          # LlamaParse / Unstructured -> Document theo trang
│   │   │   │   └── chunker.py         # Chia hai tầng (tiêu đề Markdown -> câu)
│   │   │   ├── indexing/
│   │   │   │   ├── embeddings.py      # Jina embedding và "dấu vân tay" model
│   │   │   │   ├── vector_store.py    # ChromaDB persistent
│   │   │   │   └── index_manager.py   # Ingest, incremental re-index, query
│   │   │   ├── retrieval/
│   │   │   │   ├── filters.py         # resolve_scope, build_filters (metadata filtering)
│   │   │   │   └── retriever.py       # Truy hồi top-k
│   │   │   └── generation/
│   │   │       ├── prompt.py          # Prompt trả lời, tinh chỉnh, viết lại câu hỏi
│   │   │       ├── condense.py        # Condense question (hỏi tiếp)
│   │   │       ├── synthesizer.py     # Gemini và response synthesis (streaming)
│   │   │       └── citations.py       # Dựng danh sách trích dẫn
│   │   └── tests/                     # pytest
│   ├── scripts/
│   │   ├── evaluate_dense_retrieval.py   # Đánh giá truy hồi (hit@k, recall@k, MRR, độ trễ)
│   │   ├── reingest_from_nodes.py        # Nạp lại từ *_nodes.json, không cần parse lại PDF
│   │   └── gemini_chroma_smoke.py        # Kiểm tra nhanh embedding và Chroma
│   ├── evaluation/
│   │   └── dense_retrieval_pdf.jsonl     # Bộ câu hỏi đánh giá
│   ├── storage/                       # Không commit: uploads/, chroma_db/
│   └── .env                           # Không commit: khóa API và cấu hình
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── api.js                         # Tầng giao tiếp backend (kèm chế độ mock)
│   └── app.js                         # Giao diện, Markdown/KaTeX, lịch sử chat
├── RequirementDocument.md
└── README.md
```

---

## Cài đặt

### Yêu cầu

- Python 3.10 trở lên (đã kiểm thử với 3.12)
- Khóa API: **Google AI Studio** (Gemini), **Jina AI** (embedding), **LlamaCloud** (LlamaParse)
- Trình duyệt hiện đại (Chrome, Edge, Firefox)
- Tùy chọn: VS Code với extension Live Server để mở frontend

### Các bước

```powershell
git clone <url-repo>
cd LlamaIndex-RAG/backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate

pip install fastapi "uvicorn[standard]" python-multipart python-dotenv pydantic `
            chromadb llama-index-core llama-index-vector-stores-chroma `
            llama-index-embeddings-jinaai llama-index-llms-google-genai `
            llama-index-readers-llama-parse llama-cloud pymupdf "unstructured[pdf]" `
            pytest httpx
```

> **Lưu ý:** OCR dự phòng của Unstructured cần cài thêm **Tesseract** (gói ngôn ngữ `vie`) và **Poppler** trên hệ điều hành. Thiếu hai công cụ này thì hệ thống vẫn chạy với LlamaParse; chỉ đường dự phòng OCR không hoạt động.

---

## Cấu hình môi trường

Tạo file `backend/.env` (không commit, đã có trong `.gitignore`):

```env
# Gemini: sinh câu trả lời và viết lại câu hỏi
GOOGLE_API_KEY=<khóa Google AI Studio>
GEMINI_LLM_MODEL=gemini-3.7-flash

# Jina: embedding
EMBED_PROVIDER=jina
JINA_API_KEY=<khóa Jina AI>
JINA_EMBED_MODEL=jina-embeddings-v3

# LlamaCloud: parse PDF sang Markdown
LLAMA_CLOUD_API_KEY=<khóa LlamaCloud>
```

| Biến | Bắt buộc | Mô tả |
|---|:---:|---|
| `GOOGLE_API_KEY` | Có | Khóa Gemini |
| `GEMINI_LLM_MODEL` | Có | Model Gemini dùng để trả lời và viết lại câu hỏi |
| `EMBED_PROVIDER` | Không | Hiện chỉ hỗ trợ `jina` (mặc định) |
| `JINA_API_KEY` | Có | Khóa Jina AI |
| `JINA_EMBED_MODEL` | Không | Mặc định `jina-embeddings-v3` |
| `LLAMA_CLOUD_API_KEY` | Có | Khóa LlamaParse |

> **Lưu ý:** đổi model embedding sau khi đã có dữ liệu sẽ bị chặn, vì vector cũ lệch số chiều. Khi đó cần xóa `backend/storage/chroma_db/` và nạp lại tài liệu.

---

## Chạy ứng dụng

### Backend (cổng 8000)

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

- Kiểm tra: <http://127.0.0.1:8000/health> trả về `{"status": "ok"}`
- Tài liệu API tự sinh (Swagger UI): <http://127.0.0.1:8000/docs>

### Frontend (cổng 5500)

Backend chỉ cho phép CORS từ `http://127.0.0.1:5500` và `http://localhost:5500`, vì vậy frontend phải chạy ở cổng 5500:

- VS Code Live Server: chuột phải `frontend/index.html`, chọn *Open with Live Server*; hoặc
- Python:
  ```powershell
  python -m http.server 5500 --directory frontend
  ```
  sau đó mở <http://127.0.0.1:5500>.

### Chế độ demo

Đặt `USE_MOCK: true` trong `frontend/api.js` để xem giao diện với dữ liệu giả lập, không cần backend.

---

## Hướng dẫn sử dụng

1. **Tải sách lên:** kéo thả file PDF vào cột Tài liệu hoặc bấm để chọn. Trạng thái lần lượt là đang tải lên, đang xử lý và sẵn sàng.
2. **Chọn phạm vi:** đánh dấu các sách muốn hỏi. Không chọn sách nào nghĩa là tìm trong tất cả sách đã tải lên.
3. **Đặt câu hỏi** ở ô nhập (Enter để gửi, Shift+Enter để xuống dòng) hoặc chọn một gợi ý ở màn hình chào.
4. **Xem trích dẫn:** bấm `[n]` trong câu trả lời để mở đoạn sách tương ứng, kèm số trang, ở cột bên phải.
5. **Hỏi tiếp** tự nhiên, ví dụ: "Sự nóng chảy là gì?", sau đó "Còn quá trình ngược lại thì sao?". Dòng trạng thái hiển thị câu hỏi đã được hệ thống hiểu lại.
6. **Thao tác khác:** sửa câu hỏi, tạo lại câu trả lời, sao chép, dừng khi đang trả lời, xóa cuộc trò chuyện (bấm hai lần để xác nhận).

| Phím tắt | Tác dụng |
|---|---|
| `Ctrl` / `Cmd` + `B` | Bật/tắt cột trích dẫn |
| `Esc` | Dừng câu trả lời đang sinh hoặc đóng ngăn kéo |

---

## Pipeline RAG

### 1. Nạp tài liệu

- LlamaParse chuyển PDF sang Markdown, mỗi trang thành một `Document` (giữ bảng, tiêu đề, công thức).
- Khi LlamaParse lỗi, hệ thống dùng Unstructured (`strategy="ocr_only"`, tiếng Việt).
- Mỗi trang được gắn metadata: `file_id`, `file_name`, `page`, `document_type`, `parser` và các nhãn tùy chọn `subject`, `grade`, `series`, `volume`.

### 2. Chia đoạn

| Tầng | Công cụ | Mục đích |
|---|---|---|
| 1 | `MarkdownNodeParser` | Cắt theo tiêu đề, mỗi section là một mục trọn vẹn của sách |
| 2 | `SentenceSplitter` (700 token, overlap 64) | Section dài được cắt tiếp theo câu, giữ ngữ cảnh ở ranh giới |

Vì đầu vào đã được tách theo trang, mỗi đoạn luôn thuộc đúng một trang, nhờ đó số trang trong trích dẫn chính xác.

### 3. Embedding và lập chỉ mục

- `jina-embeddings-v3` (1024 chiều, đa ngôn ngữ) lưu vào ChromaDB persistent, collection `SGKCTST`, chỉ mục HNSW với khoảng cách cosine.
- Collection lưu "dấu vân tay" model (`jina:jina-embeddings-v3`); đổi model mà không index lại sẽ bị chặn.
- Metadata không được đưa vào embedding (vector chỉ phản ánh nội dung); LLM chỉ thấy `file_name` và `page`.
- Incremental re-index so sánh mã MD5 của từng đoạn: giữ vector của đoạn không đổi, chỉ embedding đoạn mới hoặc đã sửa, xóa đoạn không còn tồn tại.

### 4. Truy hồi

- Dense retrieval top-k (mặc định 5, tối đa 10).
- Pre-filtering theo metadata (Chroma lọc trước, sau đó mới tìm vector gần nhất): `file_id` (EQ/IN), `subject`, `grade`, `series`, khoảng trang (`page_from`, `page_to`, chỉ khi chọn đúng một sách). Các điều kiện kết hợp bằng AND.

### 5. Sinh câu trả lời

- Mỗi đoạn tìm được trở thành một evidence đánh số `Evidence [n]`, kèm tên file và số trang.
- Gemini (`temperature = 0`) trả lời theo prompt grounded:
  - chỉ dùng EVIDENCE; mọi nhận định có marker `[n]`;
  - thiếu thông tin thì nói rõ, không suy đoán;
  - nội dung tài liệu được coi là dữ liệu, không phải chỉ dẫn (chống prompt injection);
  - trình bày Markdown có phân cấp; công thức dùng `$...$` hoặc `$$...$$`.
- Response synthesis dùng chế độ COMPACT (gộp evidence vừa ngữ cảnh, tràn thì refine) và streaming.

### 6. Trích dẫn

Danh sách trích dẫn được gửi về trước câu trả lời (sự kiện `citations`). Mỗi mục gồm `id`, `source` (tên file), `page`, `excerpt` (đoạn trích đã làm sạch) và `score`.

---

## Bộ nhớ hội thoại và viết lại câu hỏi

Câu hỏi nối tiếp như "Còn chu vi của nó?" không tự đủ nghĩa: nếu tìm kiếm nguyên văn, retriever không biết "nó" chỉ đối tượng nào. Hệ thống xử lý bằng kỹ thuật **condense question**, tương đương `CondenseQuestionChatEngine` của LlamaIndex:

```
app.js       state.messages (bộ nhớ của tab)
               historyBefore(m): 6 tin gần nhất TRƯỚC câu hỏi
               (bỏ tin lỗi, câu "không tìm thấy", hậu tố "[Đã dừng]")
                    |  POST /query/stream { question, history }
                    v
schemas.py   ChatTurn: role thuộc {user, assistant}, tối đa 20.000 ký tự
                    v
query.py     có history -> condense.py
                    - format_history: bỏ marker [n], mỗi tin tối đa 1.000 ký tự
                    - CONDENSE_QUESTION_TEMPLATE -> Gemini (temperature 0)
                    - _clean: bỏ nhãn và ngoặc kép, chỉ lấy dòng đầu
                    - lỗi, rỗng hoặc dài quá 500 ký tự -> dùng câu gốc
                    v
"Còn chu vi của nó?"  ->  "Chu vi hình tròn tính thế nào?"
                    v
Retriever và Gemini cùng dùng câu đã viết lại
```

| Tham số | Giá trị | Vị trí |
|---|---|---|
| Số tin lịch sử | 6 (3 lượt hỏi–đáp) | `HISTORY_MESSAGES` (`app.js`), `MAX_HISTORY_MESSAGES` (`condense.py`) |
| Độ dài mỗi tin trong prompt | 1.000 ký tự | `MAX_CHARS_PER_MESSAGE` |
| Giới hạn câu viết lại | 500 ký tự | `MAX_REWRITE_CHARS` |
| Nơi lưu lịch sử | Bộ nhớ của tab; tải lại trang hoặc xóa cuộc trò chuyện là bắt đầu mới | `state.messages` |

- Câu hỏi đầu tiên không phát sinh thêm lần gọi LLM nào.
- Tạo lại một câu trả lời cũ chỉ gửi kèm các lượt trước nó.
- Kiểm chứng qua dòng trạng thái "Đang tìm: ..." trên giao diện và log của server:
  ```
  INFO app.rag.generation.condense: Condense question: 'Còn chu vi của nó?' -> 'Chu vi hình tròn tính thế nào?'
  ```

---

## API

Tài liệu tương tác đầy đủ tại `/docs` (Swagger UI).

| Phương thức | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/health` | Kiểm tra server còn hoạt động |
| `POST` | `/upload` | Tải PDF lên (multipart: `file`; tùy chọn `subject`, `grade`, `series`, `volume`), xử lý ngầm |
| `GET` | `/files` | Danh sách tài liệu kèm trạng thái và nhãn |
| `GET` | `/files/{file_id}/status` | Tiến độ xử lý ngầm: `processing`, `indexed` hoặc `error` |
| `POST` | `/files/{file_id}/reindex` | Cập nhật tài liệu (tùy chọn kèm PDF mới), chỉ embedding phần thay đổi |
| `PATCH` | `/files/{file_id}/metadata` | Sửa nhãn môn, lớp, bộ sách, tập; không embedding lại |
| `DELETE` | `/files/{file_id}` | Xóa PDF, metadata và vector |
| `POST` | `/query/stream` | Hỏi đáp, trả về luồng Server-Sent Events |

### POST /query/stream

Request:

```json
{
  "question": "Còn quá trình ngược lại thì sao?",
  "top_k": 5,
  "file_ids": ["2a9785bcb622455896e201b67583bc3c"],
  "history": [
    { "role": "user", "content": "Sự nóng chảy là gì?" },
    { "role": "assistant", "content": "Sự nóng chảy là quá trình chuyển từ thể rắn sang thể lỏng [1]." }
  ]
}
```

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `question` | string | 1–4.000 ký tự, bắt buộc |
| `top_k` | int | 1–10, mặc định 5 |
| `file_ids` | string[] | Bỏ trống nghĩa là tìm trong mọi sách đã tải lên |
| `subject`, `grade`, `series` | | Lọc metadata (môn thuộc whitelist, lớp 1–12) |
| `page_from`, `page_to` | int | Chỉ dùng khi chọn đúng một sách |
| `history` | ChatTurn[] | Tối đa 20 tin; `role` là `user` hoặc `assistant` |

Response (`text/event-stream`), mỗi sự kiện có dạng `data: {json}`:

| `event` | Nội dung |
|---|---|
| `status` | Trạng thái tiến trình, ví dụ "Đang tìm: ...", "Đang tổng hợp câu trả lời..." |
| `citations` | Danh sách trích dẫn `{id, source, page, excerpt, score, ...}` |
| `token` | Một phần của câu trả lời (stream) |
| `not_found` | Chưa có tài liệu hoặc không tìm thấy nội dung liên quan |
| `error` | Lỗi, ví dụ Gemini quá tải (503), hết lượt (429), sai tên model (404) |

---

## Nạp tài liệu bằng dòng lệnh

Ngoài giao diện, có thể chạy toàn bộ pipeline cho một thư mục PDF, phục vụ thử nghiệm và đánh giá:

```powershell
cd backend
# Đặt các file PDF vào app/database/data/pdf/
.\.venv\Scripts\python.exe -m app.rag.pipeline
```

Kết quả trung gian (`*.md`, `*_nodes.json`) được ghi vào `app/database/storage/pipeline_runs/`.

Nạp lại từ file nodes có sẵn, không gọi LlamaParse:

```powershell
.\.venv\Scripts\python.exe -m scripts.reingest_from_nodes <nodes.json> <file_id> [ten_hien_thi.pdf] [subject=vat_ly grade=12 ...]
```

---

## Đánh giá

Đánh giá chất lượng truy hồi trên bộ câu hỏi cố định đã gán nhãn trang đúng:

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.evaluate_dense_retrieval
```

- Dữ liệu: `backend/evaluation/dense_retrieval_pdf.jsonl`, mỗi dòng gồm `question`, `file_ids`, `relevant_pages`.
- Chỉ số với `k` thuộc {3, 5, 8, 10}: hit@k, recall@k, MRR, độ trễ p50 và p95 (ms).

---

## Kiểm thử

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest app/tests/test_chunker_metadata.py app/tests/test_filters.py `
    app/tests/test_retriever.py app/tests/test_vector_retrieval_chroma.py -v
```

| Test | Nội dung |
|---|---|
| `test_chunker_metadata.py` | Mỗi đoạn giữ đúng trang, không trộn nội dung của hai trang |
| `test_filters.py` | Dựng bộ lọc metadata; tích hợp Chroma tạm với embedding giả (không cần API) |
| `test_retriever.py` | Kiểm tra tham số đầu vào của Retriever |
| `test_vector_retrieval_chroma.py` | Truy hồi và lọc theo `file_id` trên Chroma trong bộ nhớ |

---

## Bảo mật

- Khóa API chỉ nằm trong `backend/.env` (đã được `.gitignore`), không ghi trong mã nguồn.
- Upload: kiểm tra đuôi file, magic bytes `%PDF-`, cấu trúc PDF (PyMuPDF), giới hạn 50 MB, ghi file atomic.
- Đầu vào API: Pydantic với `extra="forbid"` (từ chối trường lạ), giới hạn độ dài, whitelist môn học; `file_id` phải đúng 32 ký tự hex (ngăn path traversal).
- Prompt injection: nội dung tài liệu và lịch sử hội thoại được khai báo là dữ liệu, không phải chỉ dẫn; vai trò trong lịch sử chỉ nhận `user` hoặc `assistant`.
- XSS: frontend hiển thị Markdown bằng `createElement` và `textContent`; HTML trong câu trả lời hoặc đoạn trích không bao giờ được chèn trực tiếp vào trang.
- CORS: chỉ cho phép origin của frontend (`localhost:5500`, `127.0.0.1:5500`).

---

## Xử lý sự cố

| Triệu chứng | Nguyên nhân và cách xử lý |
|---|---|
| Frontend báo "Mất kết nối" | Backend chưa chạy ở cổng 8000, hoặc frontend không chạy ở cổng 5500 nên bị CORS chặn |
| "Gemini đang quá tải" (503) | Phía Google quá tải tạm thời; thử lại sau ít phút hoặc đổi `GEMINI_LLM_MODEL` |
| "Model Gemini đã hết lượt gọi" (429) | Hết hạn mức của model trong gói hiện tại; chờ hoặc đổi model |
| "Không tìm thấy model Gemini" (404) | Sai tên hoặc model đã ngừng cung cấp; kiểm tra `GEMINI_LLM_MODEL` |
| `EmbeddingModelMismatchError` khi khởi động | Đã đổi model embedding; xóa `backend/storage/chroma_db/` và nạp lại tài liệu |
| Upload báo lỗi parse | Kiểm tra `LLAMA_CLOUD_API_KEY`; PDF dạng ảnh scan cần Tesseract và Poppler cho OCR dự phòng |
| Không thấy log "Condense question" | Log chỉ xuất hiện từ câu hỏi thứ hai trở đi trong cùng cuộc trò chuyện |
| Sửa `.env` nhưng không có tác dụng | Khởi động lại server, vì LLM và embedding được cache |

---

## Hạn chế và hướng phát triển

### Hạn chế hiện tại

- Không có tài khoản người dùng: tài liệu được dùng chung, ai truy cập được cũng có thể xóa tài liệu.
- Lịch sử hội thoại chỉ nằm trong tab trình duyệt; tải lại trang là bắt đầu cuộc trò chuyện mới.
- Prompt trả lời chỉ nhận câu hỏi đã viết lại, không nhận lịch sử, nên các câu như "giải thích lại ý thứ hai ở trên" chưa được xử lý tốt.
- Tiến độ xử lý upload lưu trong RAM (mất khi khởi động lại server); cần chạy Uvicorn với một worker do Chroma chạy dạng embedded.
- `test_upload_metadata.py` và `test_incremental_update.py` được viết cho API upload đồng bộ trước đây và cần cập nhật.

### Hướng phát triển

- Reranking (cross-encoder) sau bước truy hồi
- Nhiều collection hoặc nhiều nguồn dữ liệu (sách giáo khoa, đề thi, tài liệu tham khảo) kèm cơ chế định tuyến
- Bảng đánh giá truy hồi trước và sau khi áp dụng condense cho câu hỏi nối tiếp
- Đánh giá faithfulness và answer relevancy của câu trả lời
- Mở PDF đúng trang từ thẻ trích dẫn
- Docker Compose, GitHub Actions (lint và test), giới hạn tần suất request

---

## Tài liệu liên quan

- [RequirementDocument.md](RequirementDocument.md): yêu cầu kỹ thuật theo từng tầng
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)
- [ChromaDB Documentation](https://docs.trychroma.com/)
