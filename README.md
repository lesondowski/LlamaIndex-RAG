# LlamaIndex RAG — Hỏi đáp sách giáo khoa

Ứng dụng hỏi đáp bằng tiếng Việt dựa trên tài liệu PDF. Người dùng tải tài liệu lên, chờ xử lý và đặt câu hỏi; câu trả lời được trả về theo luồng (SSE), kèm trích dẫn nguồn và số trang.

Backend sử dụng FastAPI, LlamaIndex và ChromaDB. Frontend sử dụng HTML, CSS và JavaScript thuần, không cần Node.js hoặc bước build.

## 1. Yêu cầu

- Git và Python 3.10 trở lên (theo `backend/pyproject.toml`).
- Kết nối Internet để gọi dịch vụ parsing, embedding và tạo câu trả lời.
- `JINA_API_KEY`: tạo embedding khi chọn Jina.
- `GOOGLE_API_KEY`: dùng Gemini để tạo câu trả lời, kể cả khi embedding bằng Jina.
- `LLAMA_CLOUD_API_KEY`: dùng LlamaParse để trích xuất nội dung PDF. Nếu parsing thất bại, ứng dụng thử chuyển sang Unstructured OCR.

Các lệnh dưới đây chạy bằng PowerShell trên Windows, bắt đầu tại thư mục gốc dự án.

## 2. Tạo môi trường và cài thư viện

Clone repository bằng URL của dự án rồi chuyển vào thư mục `LlamaIndex-RAG`. Nếu đã có source code trên máy, bắt đầu từ bước tạo môi trường:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ./backend
python -m pip install pymupdf4llm llama-index-readers-llama-parse "unstructured[pdf]"
python -m pip check
```

Lệnh cài bổ sung cung cấp các thư viện mà `loader.py` đang import nhưng chưa được khai báo đầy đủ trong `backend/pyproject.toml`.

Nếu PowerShell chặn kích hoạt môi trường, có thể dùng trực tiếp Python trong `.venv` để chạy từng lệnh, ví dụ:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ./backend
```

File `requirements.txt` ở thư mục gốc chứa thêm thư viện cho các luồng xử lý/thử nghiệm khác và chưa đủ để chạy backend độc lập. Hướng dẫn này sử dụng dependencies trong `backend/pyproject.toml` và các gói loader bổ sung phía trên.

### OCR dự phòng cho PDF scan

Loader dự phòng gọi `partition_pdf` với `strategy="ocr_only"` và `languages=["vie"]`. Để sử dụng luồng này, cần cài thêm trên hệ điều hành:

- Tesseract OCR, có dữ liệu ngôn ngữ tiếng Việt (`vie.traineddata`).
- Poppler, có các công cụ chuyển PDF thành ảnh.

Thêm thư mục chứa các chương trình này vào `PATH`, mở lại terminal và kiểm tra:

```powershell
tesseract --version
tesseract --list-langs
pdftoppm -v
```

Danh sách ngôn ngữ Tesseract phải có `vie`. Chỉ cài các gói Python không thay thế việc cài Tesseract và Poppler trên hệ điều hành.

## 3. Cấu hình biến môi trường

Tạo file `backend/.env` với nội dung sau và thay các giá trị `your_...` bằng API key của bạn:

```dotenv
# Trích xuất nội dung PDF
LLAMA_CLOUD_API_KEY=your_llama_cloud_api_key

# Gemini tạo câu trả lời
GOOGLE_API_KEY=your_google_api_key
GEMINI_LLM_MODEL=gemini-2.5-flash

# Jina tạo embedding cho tài liệu và câu hỏi
EMBED_PROVIDER=jina
JINA_API_KEY=your_jina_api_key
JINA_EMBED_MODEL=jina-embeddings-v3
JINA_EMBED_DIMENSIONS=1024
JINA_EMBED_BATCH_SIZE=16

# Collection dành riêng cho cấu hình embedding này
CHROMA_COLLECTION=sgk_jina_v3_1024
```

`app/main.py` nạp trực tiếp `backend/.env`. Hướng dẫn này chạy backend từ thư mục `backend` để các lời gọi `load_dotenv()` trong loader cũng sử dụng cùng file cấu hình. Không commit API key; `.env` đã được bỏ qua trong `.gitignore`.

| Biến | Ý nghĩa / giá trị mặc định trong code |
| --- | --- |
| `EMBED_PROVIDER` | `gemini` hoặc `jina`; mặc định là `gemini`, nên cần đặt rõ `jina` khi dùng cấu hình trên. |
| `GEMINI_LLM_MODEL` | Model tạo câu trả lời; mặc định `gemini-2.5-flash`. |
| `GEMINI_EMBED_MODEL` | Model embedding khi chọn Gemini; mặc định `gemini-embedding-001`. |
| `JINA_EMBED_MODEL` | Triển khai Jina hiện tại chỉ hỗ trợ `jina-embeddings-v3`. |
| `JINA_EMBED_DIMENSIONS` | Mặc định `1024`; cho phép `32`, `64`, `128`, `256`, `512`, `768`, `1024`. |
| `JINA_EMBED_BATCH_SIZE` | Mặc định `16`; từ `1` đến `64`. |
| `CHROMA_COLLECTION` | Tên collection lưu vector; mặc định `SGKCTST` nếu không cấu hình. |

Để dùng Gemini cho cả embedding và tạo câu trả lời, thay cấu hình embedding bằng:

```dotenv
EMBED_PROVIDER=gemini
GEMINI_EMBED_MODEL=gemini-embedding-001
CHROMA_COLLECTION=sgk_gemini_embedding_001
```

Khi đổi provider, model hoặc số chiều embedding, hãy chọn collection mới và tải/index lại tài liệu. ChromaDB kiểm tra fingerprint embedding và sẽ chặn collection có cấu hình không khớp. Không sửa metadata collection để ép dùng lại vector cũ.

## 4. Chạy backend

Trong terminal đã kích hoạt `.venv`, từ thư mục gốc:

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Giữ terminal này chạy. Các địa chỉ kiểm tra:

- Health check: <http://127.0.0.1:8000/health>, trả về `{"status":"ok"}`.
- Swagger UI: <http://127.0.0.1:8000/docs>.

Health check chỉ xác nhận API đang chạy; chưa xác nhận API key, OCR hoặc pipeline hỏi đáp hoạt động đầy đủ.

## 5. Chạy frontend

Mở terminal thứ hai tại thư mục gốc dự án:

```powershell
.\.venv\Scripts\python.exe -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Truy cập <http://127.0.0.1:5500>. Cũng có thể dùng VS Code Live Server tại cổng `5500`, với thư mục `frontend` làm thư mục phục vụ.

Frontend mặc định gọi API tại `http://127.0.0.1:8000` và đã đặt `USE_MOCK: false` trong `frontend/api.js`. Backend cho phép CORS từ `http://127.0.0.1:5500` và `http://localhost:5500`. Nếu đổi cổng, cần cập nhật cấu hình tương ứng trong `frontend/api.js` và `backend/app/main.py`.

## 6. Kiểm tra luồng sử dụng

1. Mở frontend và kiểm tra trạng thái kết nối backend.
2. Tải lên một PDF nhỏ, có nội dung rõ ràng. Giới hạn hiện tại là **50 MB/file**.
3. Chờ trạng thái xử lý chuyển sang hoàn tất. Upload chạy nền; nhận phản hồi upload thành công chưa có nghĩa là tài liệu đã index xong.
4. Chọn tài liệu và đặt câu hỏi dựa trên nội dung của tài liệu đó.
5. Kiểm tra câu trả lời, trích dẫn và số trang.

PDF và metadata được lưu tại `backend/storage/uploads/`; vector được lưu tại `backend/storage/chroma_db/`. Không cần chạy ChromaDB server riêng. Các thư mục lưu trữ được tạo khi backend sử dụng chúng.

## 7. API chính

| Method | Endpoint | Chức năng |
| --- | --- | --- |
| GET | `/health` | Kiểm tra backend đang chạy. |
| POST | `/upload` | Upload PDF và xử lý nền. |
| GET | `/files` | Liệt kê tài liệu. |
| GET | `/files/{file_id}/status` | Theo dõi tiến trình xử lý. |
| POST | `/files/{file_id}/reindex` | Cập nhật tài liệu và index các chunk thay đổi. |
| PATCH | `/files/{file_id}/metadata` | Cập nhật metadata tài liệu. |
| DELETE | `/files/{file_id}` | Xóa tài liệu và vector liên quan. |
| POST | `/query/stream` | Hỏi đáp và nhận kết quả theo SSE. |

Xem schema request và thử API trực tiếp tại `/docs`.

## 8. Cấu trúc dự án

```text
LlamaIndex-RAG/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI và CORS
│   │   ├── routers/         # Upload, quản lý tài liệu, hỏi đáp
│   │   ├── rag/
│   │   │   ├── ingestion/   # Đọc PDF và chia chunk
│   │   │   ├── indexing/    # Embedding và ChromaDB
│   │   │   ├── retrieval/   # Truy hồi và lọc metadata
│   │   │   └── generation/  # Tổng hợp câu trả lời và trích dẫn
│   │   └── tests/
│   ├── scripts/
│   ├── storage/             # Dữ liệu sinh ra khi chạy
│   └── pyproject.toml
├── frontend/                # index.html, api.js, app.js, style.css
├── requirements.txt
└── README.md
```

## 9. Chạy kiểm thử

Từ thư mục `backend`, trong môi trường đã cài dependencies:

```powershell
python -m pip install pytest
python -m pytest app/tests
```

Một số kiểm thử tích hợp có thể yêu cầu thêm cấu hình hoặc dịch vụ; xem nội dung từng test khi có lỗi. Kiểm thử không thay thế việc thử upload và hỏi đáp với API key thực tế.

## 10. Lỗi thường gặp

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| `No module named ...` | Kiểm tra đang dùng Python trong `.venv`, chạy lại các lệnh cài thư viện ở bước 2. |
| `No module named app` | Chạy Uvicorn từ thư mục `backend` theo bước 4. |
| Thiếu API key | Kiểm tra `backend/.env`, thay placeholder và khởi động lại backend. |
| Frontend không kết nối / lỗi CORS | Kiểm tra cổng `8000`, `5500`, địa chỉ API và danh sách `allow_origins`. |
| LlamaParse và Unstructured đều thất bại | Xem log backend; kiểm tra LlamaCloud key/quota, Tesseract, Poppler và ngôn ngữ `vie`. |
| `EmbeddingModelMismatchError` | Chọn collection mới khớp cấu hình embedding rồi index lại tài liệu. |
| API trả `429` | Kiểm tra quota/rate limit của dịch vụ; với Jina có thể giảm batch size. |
| Upload trả `413` | PDF vượt giới hạn 50 MB; giảm kích thước hoặc chia tài liệu. |
| Không tìm thấy nội dung | Xác nhận tài liệu đã index xong, chọn đúng nguồn và thử câu hỏi có thông tin trong PDF. |

Tiến trình upload hiện được lưu trong RAM. Sau khi backend khởi động lại, trạng thái tiến trình trước đó không được giữ nguyên; cần kiểm tra log và kết quả hỏi đáp để xác nhận tài liệu đã được index.
