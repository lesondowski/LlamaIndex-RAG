from pathlib import Path

# app/config.py -> parents[0] = app, parents[1] = backend
BASE_DIR = Path(__file__).resolve().parents[1]

UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)   # phòng trường hợp clone về mà thiếu folder

MAX_FILE_SIZE = 50 * 1024 * 1024   # khớp CONFIG.MAX_FILE_SIZE ở api.js