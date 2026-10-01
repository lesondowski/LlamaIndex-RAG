# ============================================================================
# main.py — Điểm vào (entry point) của ứng dụng FastAPI
# Chạy bằng: uvicorn app.main:app --reload --port 8000
#   `app.main` là đường dẫn module, `app` (sau dấu :) là tên biến FastAPI bên dưới.
# ============================================================================
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import router từ module con. Import sau khi config đã chạy nên UPLOAD_DIR
# chắc chắn đã được tạo (config.py thực thi mkdir ngay khi được import).
from app.routers import upload

# Khởi tạo ứng dụng. `title` hiển thị trong tài liệu tự sinh tại /docs (Swagger UI).
app = FastAPI(title="Financial RAG")

# CORS (Cross-Origin Resource Sharing): trình duyệt mặc định chặn trang ở origin A
# gọi API ở origin B. Khai báo danh sách origin được phép để frontend (Live Server
# chạy cổng 5500) gọi được backend (cổng 8000).
# Dùng danh sách cụ thể thay vì ["*"] để không mở API cho mọi website trên internet.
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


# Gắn router upload vào app chính. Endpoint POST /upload bắt đầu hoạt động từ đây.
app.include_router(upload.router)