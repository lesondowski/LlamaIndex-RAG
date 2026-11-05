# Chatbot RAG hỏi đáp sách giáo khoa với LlamaIndex

Ứng dụng web cho phép tải sách giáo khoa dạng PDF, đặt câu hỏi bằng tiếng Việt và nhận câu trả lời bám theo nội dung sách, kèm trích dẫn số trang để đối chiếu. Hệ thống hiểu được câu hỏi nối tiếp và có lớp từ chối khi sách không có thông tin.

Đồ án của Nhóm 25, học phần Các công nghệ lập trình hiện đại, Trường Đại học Sài Gòn.

> **Phiên bản nộp:** commit `339819d` trên nhánh `hnnnhu`. Nhánh `main` là một phiên bản khác (chia Node 700/126); các hướng dẫn và số liệu trong tài liệu này áp dụng cho `hnnnhu`.

## Mục lục

- [Tính năng](#tính-năng)
- [Kiến trúc](#kiến-trúc)
- [Công nghệ](#công-nghệ)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Cài đặt](#cài-đặt)
- [Cấu hình](#cấu-hình)
- [Chạy ứng dụng](#chạy-ứng-dụng)
- [Sử dụng](#sử-dụng)
- [Luồng xử lý](#luồng-xử-lý)
- [API](#api)
- [Nạp tài liệu bằng dòng lệnh](#nạp-tài-liệu-bằng-dòng-lệnh)
- [Đánh giá](#đánh-giá)
- [Kiểm thử](#kiểm-thử)
- [Bảo mật](#bảo-mật)
- [Hạn chế đã biết](#hạn-chế-đã-biết)
- [Xử lý sự cố](#xử-lý-sự-cố)
- [Hướng phát triển](#hướng-phát-triển)
- [Tài liệu liên quan](#tài-liệu-liên-quan)

## Tính năng

| Nhóm | Mô tả |
|---|---|
| Nạp tài liệu | Tải PDF qua giao diện; kiểm tra định dạng và giới hạn 50 MB; đọc bằng LlamaParse, dự phòng Unstructured; xử lý nền và theo dõi tiến độ |
| Chia Node | Chia theo tiêu đề Markdown, sau đó theo câu (1.400 token, overlap 252); mỗi Node thuộc đúng một trang |
| Lập chỉ mục | Embedding `jina-embeddings-v3` (1024 chiều), lưu bền trong ChromaDB (HNSW, cosine) |
| Cập nhật tăng dần | So mã MD5 để chỉ embedding Node mới hoặc đã đổi; sửa nhãn không cần embedding lại |
| Truy hồi | Tìm top-k theo vector; lọc trước theo sách, môn, lớp, bộ sách và khoảng trang |
| Trả lời | Gemini với prompt chỉ dùng nội dung được cung cấp; trả dần từng phần qua Server-Sent Events |
| Trích dẫn | Marker `[n]` mở đúng đoạn sách, kèm tên tệp và số trang |
| Hỏi tiếp | Viết lại câu hỏi nối tiếp thành câu tự đủ nghĩa từ 6 tin gần nhất |
| Giao diện | Markdown, công thức KaTeX, bảng; nền sáng và tối; chế độ giả lập không cần backend |

## Kiến trúc

```text
Trình duyệt (frontend/)
  index.html, style.css, app.js, api.js
        |                                   |
        | /upload, /files                   | /query/stream (SSE)
        v                                   v
Backend FastAPI (backend/app/)
  routers/upload.py                   routers/query.py
    kiểm tra PDF, lưu tệp               resolve_scope: phạm vi, lọc metadata
    tác vụ nền:                         condense_question: khi có lịch sử
      loader: LlamaParse/Unstructured   Retriever: Chroma top-k
      Chunking: tiêu đề, rồi câu        build_citations: gửi trích dẫn trước
      IndexManager: Jina, Chroma        synthesizer: Gemini, gửi từng token
        |                                   |
        v                                   v
  backend/storage/uploads/            backend/storage/chroma_db/
  PDF và tệp JSON nhãn                vector, collection SGKCTST
```

Chroma chạy nhúng trong tiến trình API, nên ứng dụng phải chạy với **một worker**.

## Công nghệ

Phiên bản đầy đủ nằm trong [`backend/requirements-lock.txt`](backend/requirements-lock.txt).

| Thành phần | Công nghệ | Phiên bản đã kiểm thử |
|---|---|---|
| Ngôn ngữ | Python | 3.12.10 |
| Framework RAG | `llama-index-core` | 0.14.25 |
| API | FastAPI, Uvicorn, Pydantic | 0.142.2, 0.54.0, 2.13.5 |
| Đọc PDF | LlamaParse, Unstructured, PyMuPDF | 0.6.94, 0.27.16, 1.28.2 |
| Embedding | `llama-index-embeddings-jinaai` | 0.7.0 |
| Mô hình ngôn ngữ | `llama-index-llms-google-genai` | 0.11.3 |
| Kho vector | ChromaDB | 1.5.9 |
| Giao diện | HTML, CSS, JavaScript thuần, KaTeX | KaTeX 0.16.8 |

## Cấu trúc thư mục

```text
LlamaIndex-RAG/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI, CORS, logging, /health
│   │   ├── config.py                # Thư mục tải lên, giới hạn dung lượng
│   │   ├── schemas.py               # Kiểm tra đầu vào của câu hỏi
│   │   ├── routers/
│   │   │   ├── upload.py            # /upload, /files và các thao tác trên tài liệu
│   │   │   └── query.py             # /query/stream
│   │   ├── rag/
│   │   │   ├── types.py             # Khóa metadata, danh sách môn, phạm vi tìm
│   │   │   ├── pipeline.py          # Chạy pipeline từ dòng lệnh
│   │   │   ├── ingestion/           # loader.py, chunker.py
│   │   │   ├── indexing/            # embeddings.py, vector_store.py, index_manager.py
│   │   │   ├── retrieval/           # filters.py, retriever.py
│   │   │   └── generation/          # prompt.py, condense.py, synthesizer.py, citations.py
│   │   └── tests/                   # pytest
│   ├── scripts/                     # Đánh giá, dựng chỉ mục đo, kiểm tra lưu bền
│   ├── evaluation/                  # Bộ câu hỏi có nhãn trang
│   ├── storage/                     # Dữ liệu chạy: uploads/, chroma_db/ (không commit)
│   ├── requirements-lock.txt        # Phiên bản đã kiểm thử
│   └── .env                         # Khóa API (không commit)
├── frontend/                        # index.html, style.css, api.js, app.js
├── RequirementDocument.md
└── README.md
```

## Cài đặt

**Yêu cầu:**

- Python 3.12 (đã kiểm thử 3.12.10 trên Windows 11)
- Khóa API của Google AI Studio (Gemini), Jina AI và LlamaCloud
- Trình duyệt Chrome, Edge hoặc Firefox

Các lệnh dưới đây dùng PowerShell:

```powershell
git clone <url-repo>
cd LlamaIndex-RAG
git checkout hnnnhu
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-lock.txt
```

> Dùng `backend/requirements-lock.txt`. Tệp `requirements.txt` ở thư mục gốc có nhiều gói trên một dòng nên `pip` báo lỗi cú pháp.

> OCR dự phòng của Unstructured cần cài thêm **Tesseract** (gói ngôn ngữ `vie`) và **Poppler**. Thiếu hai công cụ này, LlamaParse vẫn hoạt động; chỉ đường dự phòng không chạy được.

## Cấu hình

Kho mã chưa có `.env.example`. Hãy tạo tệp `backend/.env` với nội dung sau rồi điền khóa của bạn. Tệp này đã được `.gitignore` chặn, không commit.

```env
# Gemini: sinh câu trả lời và viết lại câu hỏi
GOOGLE_API_KEY=<khóa Google AI Studio>
GEMINI_LLM_MODEL=gemini-3.5-flash

# Jina: embedding
EMBED_PROVIDER=jina
JINA_API_KEY=<khóa Jina AI>
JINA_EMBED_MODEL=jina-embeddings-v3

# LlamaCloud: đọc PDF
LLAMA_CLOUD_API_KEY=<khóa LlamaCloud>
```

| Biến | Bắt buộc | Mô tả |
|---|---|---|
| `GOOGLE_API_KEY` | Có | Khóa Gemini |
| `GEMINI_LLM_MODEL` | Không | Model trả lời và viết lại câu hỏi. Bỏ trống sẽ dùng `gemini-2.5-flash`; các số đo chính của đồ án dùng `gemini-3.5-flash` |
| `GEMINI_FALLBACK_MODELS` | Không | Danh sách model dự phòng, cách nhau bằng dấu phẩy. Hiện **chưa được nối** vào luồng trả lời |
| `EMBED_PROVIDER` | Không | Hiện chỉ hỗ trợ `jina` (mặc định) |
| `JINA_API_KEY` | Có | Khóa Jina AI |
| `JINA_EMBED_MODEL` | Không | Mặc định `jina-embeddings-v3` |
| `LLAMA_CLOUD_API_KEY` | Có | Khóa LlamaParse |

> Đổi model embedding khi đã có dữ liệu sẽ bị chặn bằng `EmbeddingModelMismatchError`. Khi đó cần xóa `backend/storage/chroma_db/` và nạp lại tài liệu.

## Chạy ứng dụng

**Backend, cổng 8000:** chạy tại thư mục `backend`.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Kiểm tra tại <http://127.0.0.1:8000/health> (kết quả `{"status": "ok"}`). Tài liệu API nằm ở <http://127.0.0.1:8000/docs>.

**Giao diện, cổng 5500:** mở cửa sổ khác, chạy tại thư mục gốc của kho.

```powershell
python -m http.server 5500 --directory frontend
```

Sau đó mở <http://127.0.0.1:5500>. Backend chỉ cho phép CORS từ `127.0.0.1:5500` và `localhost:5500`, nên giao diện phải chạy đúng cổng này.

**Chế độ giả lập:** đặt `USE_MOCK: true` trong `frontend/api.js` để xem giao diện mà không cần backend.

## Sử dụng

1. **Tải sách:** kéo thả PDF vào cột Tài liệu hoặc bấm để chọn, rồi chờ trạng thái sẵn sàng.
2. **Chọn phạm vi:** đánh dấu các sách cần hỏi. Không chọn sách nào nghĩa là tìm trong tất cả.
3. **Đặt câu hỏi:** Enter để gửi, Shift+Enter để xuống dòng.
4. **Xem nguồn:** bấm `[n]` để mở đoạn sách và số trang ở khung bên phải.
5. **Hỏi tiếp:** ví dụ "Sự nóng chảy là gì?" rồi "Còn quá trình ngược lại thì sao?".

| Phím tắt | Tác dụng |
|---|---|
| `Ctrl`/`Cmd` + `B` | Bật hoặc tắt khung trích dẫn |
| `Esc` | Dừng câu trả lời đang sinh hoặc đóng ngăn kéo |

Giao diện chưa có ô nhập nhãn môn, lớp. Muốn gắn nhãn, hãy gọi API: truyền nhãn khi `POST /upload` hoặc dùng `PATCH /files/{file_id}/metadata`.

## Luồng xử lý

1. **Nạp:** LlamaParse chuyển mỗi trang PDF thành một `Document` kèm metadata `file_id`, `file_name`, `page` và các nhãn tùy chọn. Khi LlamaParse lỗi, hệ thống dùng Unstructured.
2. **Chia Node:** `MarkdownNodeParser` cắt theo tiêu đề, `SentenceSplitter` cắt tiếp các mục dài. Vì đầu vào đã tách theo trang, số trang trong trích dẫn được giữ theo từng Node.
3. **Lập chỉ mục:** metadata không đưa vào văn bản embedding. Collection lưu dấu vân tay của model để phát hiện việc đổi model.
4. **Viết lại câu hỏi:** khi có lịch sử, `condense_question` viết lại câu hỏi; nếu lỗi, hệ thống dùng câu gốc.
5. **Truy hồi:** lấy top-k (mặc định 5, tối đa 10), kết hợp các bộ lọc metadata bằng AND.
6. **Trả lời:** các Node được đánh số `[n]`, Gemini trả lời theo prompt: chỉ dùng bằng chứng, thiếu thông tin thì nói rõ, coi nội dung tài liệu là dữ liệu chứ không phải chỉ dẫn. Trích dẫn được gửi trước câu trả lời.

## API

Tài liệu tương tác nằm ở `/docs`.

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| `GET` | `/health` | Kiểm tra máy chủ |
| `POST` | `/upload` | Tải PDF, xử lý nền. Form: `file`; tùy chọn `subject`, `grade`, `series`, `volume`, `override_file_id` |
| `GET` | `/files` | Danh sách tài liệu |
| `GET` | `/files/{file_id}/status` | Tiến độ: `processing`, `indexed` hoặc `error` |
| `POST` | `/files/{file_id}/reindex` | Cập nhật tăng dần (xem [Hạn chế đã biết](#hạn-chế-đã-biết)) |
| `PATCH` | `/files/{file_id}/metadata` | Sửa nhãn, không embedding lại |
| `DELETE` | `/files/{file_id}` | Xóa PDF, JSON và vector |
| `POST` | `/query/stream` | Hỏi đáp, trả về Server-Sent Events |

**Ví dụ `POST /query/stream`:**

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

| Trường | Ràng buộc |
|---|---|
| `question` | Bắt buộc, 1–4.000 ký tự |
| `top_k` | 1–10, mặc định 5 |
| `file_ids` | Bỏ trống nghĩa là tìm trong mọi sách |
| `subject`, `grade`, `series` | Lọc metadata; `grade` từ 1 đến 12 |
| `page_from`, `page_to` | Chỉ dùng khi chọn đúng một sách |
| `history` | Tối đa 20 tin; `role` là `user` hoặc `assistant` |

Trường lạ sẽ bị từ chối với mã 422. Môn học phải thuộc: `toan`, `vat_ly`, `hoa_hoc`, `sinh_hoc`, `ngu_van`, `lich_su`, `dia_ly`, `tin_hoc`, `tieng_anh`, `gdcd`, `khac`.

**Các sự kiện trả về:**

| `event` | Nội dung |
|---|---|
| `status` | Trạng thái tiến trình |
| `citations` | Danh sách nguồn: tệp, trang, đoạn trích, điểm |
| `token` | Một phần câu trả lời |
| `not_found` | Chưa có tài liệu hoặc không tìm thấy nội dung |
| `error` | Thông báo lỗi, ví dụ Gemini hết lượt (429), quá tải (503), sai tên model (404) |

## Nạp tài liệu bằng dòng lệnh

Chạy tại thư mục `backend`. Đặt PDF vào `app/database/data/pdf/`; kết quả trung gian được ghi vào `app/database/storage/pipeline_runs/`.

```powershell
.\.venv\Scripts\python.exe -m app.rag.pipeline
```

Nạp lại từ tệp Node có sẵn, không gọi LlamaParse:

```powershell
.\.venv\Scripts\python.exe -m scripts.reingest_from_nodes <nodes.json> <file_id> [ten_hien_thi.pdf] [subject=vat_ly grade=12 ...]
```

## Đánh giá

Chỉ số tính theo trang: hit@k, recall@k, MRR@k và độ trễ. Bộ câu hỏi nằm ở `backend/evaluation/dense_retrieval_pdf.jsonl`. Chạy tại thư mục `backend`:

```powershell
# Dựng chỉ mục đánh giá riêng, không đụng kho của ứng dụng
python -m scripts.build_eval_index --pdf <đường dẫn PDF> --chunk-size 1400 --overlap 252

# Đánh giá; mỗi lần chạy lưu manifest.json, raw.jsonl và summary.md
python -m scripts.run_retrieval_eval --chroma-dir storage/eval_indexes/c1400_o252 --collection eval_c1400_o252 --file-id <file_id của index> --name eval_c1400_o252
```

Kết quả chính trong báo cáo đồ án:

| Chỉ số | Kết quả |
|---|---|
| hit@5, MRR@5 trên tập test (35 câu) | 0,943; 0,595 |
| hit@5 trên 34 câu Vật lí, cấu hình 1.400/252 | 0,853 (29/34) |
| Câu nối tiếp tìm đúng trang: trước và sau khi viết lại | 5/13 và 11/13 |
| Độ trễ truy hồi p50, p95 | 443 ms, 1.043 ms |

Embedding câu hỏi của Jina không hoàn toàn tất định, nên kết quả có thể lệch nhẹ giữa các lần chạy. Phần lớn log đo và bộ câu hỏi mở rộng 82 câu không nằm trong commit này mà được nộp trong gói minh chứng.

## Kiểm thử

Chạy tại thư mục `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest app/tests/test_chunker_metadata.py app/tests/test_filters.py app/tests/test_retriever.py app/tests/test_vector_retrieval_chroma.py -v
```

Lệnh trên chạy 34 ca và không cần khóa API.

| Tệp | Nội dung | Trạng thái |
|---|---|---|
| `test_chunker_metadata.py` | Node giữ đúng trang, không trộn hai trang | Đạt |
| `test_filters.py` | Dựng bộ lọc; tích hợp Chroma tạm với embedding giả | Đạt |
| `test_retriever.py` | Kiểm tra tham số của Retriever | Đạt |
| `test_vector_retrieval_chroma.py` | Truy hồi và lọc theo `file_id` | Đạt |
| `test_upload_metadata.py` | Tải lên kèm nhãn | 1 đạt, 9 lỗi do fixture lỗi thời |
| `test_incremental_update.py` | Tích hợp tải lên, cập nhật | **Không chạy:** ghi vào `storage/` thật |

## Bảo mật

- Khóa API chỉ nằm trong `backend/.env`, không ghi trong mã nguồn.
- Tải lên: kiểm tra đuôi tệp, magic bytes `%PDF-`, cấu trúc PDF, giới hạn 50 MB; ghi qua tệp tạm.
- API: Pydantic với `extra="forbid"`, giới hạn độ dài, danh sách môn cho phép; `file_id` phải đúng 32 ký tự hex.
- Prompt injection: nội dung tài liệu và lịch sử hội thoại được khai báo là dữ liệu, không phải chỉ dẫn.
- CORS: chỉ cho phép origin của giao diện.
- **Chưa có:** đăng nhập, phân quyền, giới hạn tần suất. Ứng dụng chỉ nên chạy cục bộ.

## Hạn chế đã biết

Các lỗi dưới đây đã được tái hiện trong báo cáo đồ án (Chương 10, 12) và **chưa được sửa** trong phiên bản nộp.

| Vấn đề | Ảnh hưởng | Cách tránh |
|---|---|---|
| Tải đè lên sách đã có, gặp lỗi embedding | Mất sách đó: cả Node, PDF và JSON | Không tải đè; sao lưu `backend/storage/` trước khi thử |
| `POST /files/{id}/reindex` kèm tệp mới | Luôn lỗi 422 | Chỉ dùng route này không kèm tệp |
| Cập nhật tăng dần trên Node giữ lại | Số trang trong trích dẫn có thể sai; Node trùng chữ bị gộp | Nạp lại sách thay vì cập nhật |
| Model Gemini treo | Giao diện chờ vô hạn, không có thời gian chờ | Dùng `gemini-3.5-flash`; khởi động lại khi bị treo |
| `GEMINI_FALLBACK_MODELS` chưa nối vào luồng trả lời | Model chính lỗi thì không chuyển model | Đổi `GEMINI_LLM_MODEL` thủ công |
| PDF hỏng cấu trúc trên Windows | Lỗi 500, để lại tệp rác | Xóa tệp trong `backend/storage/uploads/` |
| Câu ngoài tài liệu | Vẫn hiện 5 trích dẫn; câu gần nội dung sách có thể vẫn được trả lời | Đối chiếu trích dẫn |
| Hai yêu cầu sửa nhãn đồng thời | Hiếm khi lỗi 500, Chroma lệch JSON | Không sửa đồng thời |
| Tệp sách lớn | Giao diện có thể báo quá thời gian sau khoảng 2 phút dù máy chủ vẫn nạp | Tải lại trang để xem trạng thái đúng |

Các giới hạn khác: chỉ nhận PDF; lịch sử hội thoại nằm trong tab trình duyệt; trạng thái nạp nằm trong RAM; chạy một worker; chưa có Docker và CI.

## Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| Giao diện báo mất kết nối | Kiểm tra backend ở cổng 8000 và giao diện ở cổng 5500 |
| Gemini quá tải (503) | Thử lại sau ít phút hoặc đổi `GEMINI_LLM_MODEL` |
| Gemini hết lượt (429) | Chờ hạn mức hoặc đổi model |
| Không tìm thấy model (404) | Kiểm tra `GEMINI_LLM_MODEL` |
| `EmbeddingModelMismatchError` | Xóa `backend/storage/chroma_db/` và nạp lại tài liệu |
| Lỗi đọc PDF | Kiểm tra `LLAMA_CLOUD_API_KEY`; OCR dự phòng cần Tesseract và Poppler |
| Lọc theo môn không có kết quả | Sách chưa gắn nhãn; dùng `PATCH /files/{file_id}/metadata` |
| Sửa `.env` không có tác dụng | Khởi động lại máy chủ |
| Lần nạp đầu chậm thêm 18–30 giây | Do lần đầu nạp thư viện `unstructured` |

## Hướng phát triển

- Sửa các lỗi ở mục [Hạn chế đã biết](#hạn-chế-đã-biết), ưu tiên toàn vẹn dữ liệu khi ghi
- Bật reranking và ngưỡng từ chối trong ứng dụng (đã có thử nghiệm)
- Thử tìm kiếm lai BM25 và vector
- Thêm `.env.example`, Docker Compose, CI, giới hạn tần suất
- Hỗ trợ DOCX, TXT, Markdown

## Tài liệu liên quan

- [RequirementDocument.md](RequirementDocument.md): yêu cầu kỹ thuật theo từng tầng
- [LlamaIndex Documentation](https://developers.llamaindex.ai/python/framework/)
- [Chroma Documentation](https://docs.trychroma.com/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
