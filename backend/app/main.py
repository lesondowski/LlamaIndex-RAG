from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from app.routers import upload, query


# Khởi tạo ứng dụng. `title` hiển thị trong tài liệu tự sinh tại /docs (Swagger UI).
app = FastAPI(title="Hỏi đáp Sách Giáo Khoa RAG")
app.add_middleware(
    CORSMiddleware,
    # Origin là bộ ba giao thức + host + cổng của TRANG WEB gửi request (không phải của API).
    # "127.0.0.1" và "localhost" là hai origin khác nhau, nên liệt kê cả hai để mở Live Server
    # bằng địa chỉ nào cũng chạy.
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_methods=["*"],   # cho phép POST và OPTIONS (preflight)
    allow_headers=["*"],
)


@app.get("/health")
def health():
    # Endpoint kiểm tra sống (liveness). api.js gọi checkHealth() vào đây để hiển
    # thị huy hiệu trạng thái kết nối; chỉ cần phản hồi được là coi như "đã kết nối".
    return {"status": "ok"}
app.include_router(upload.router)
app.include_router(query.router)