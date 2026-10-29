"""
Cấu hình đường dẫn và giới hạn dùng chung cho toàn bộ backend.
"""
from pathlib import Path

# app/config.py -> parents[0] = app, parents[1] = backend
BASE_DIR = Path(__file__).resolve().parents[1]

# Tạo ngay khi import để mọi module khác dùng được mà không cần kiểm tra tồn tại.
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Khớp CONFIG.MAX_FILE_SIZE ở api.js — đổi một chỗ phải đổi cả hai.
MAX_FILE_SIZE = 50 * 1024 * 1024