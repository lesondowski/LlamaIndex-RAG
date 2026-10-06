"""
Điểm vào của ứng dụng FastAPI.

Chạy bằng: uvicorn app.main:app --reload --port 8000
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Nạp backend/.env bằng đường dẫn tường minh (main.py nằm ở backend/app/ nên
# parents[1] là backend/). load_dotenv() không tham số dò theo thư mục làm việc
# nên có thể không thấy .env khi chạy qua debugger hoặc từ thư mục khác.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import upload

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger(__name__)

app = FastAPI(title="Financial RAG")

app.add_middleware(
    CORSMiddleware,
    # Origin là bộ ba giao thức + host + cổng của trang web gửi request. "127.0.0.1"
    # và "localhost" là hai origin khác nhau trong mắt trình duyệt, nên liệt kê cả hai.
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Endpoint kiểm tra sống (liveness)."""
    return {"status": "ok"}


app.include_router(upload.router)