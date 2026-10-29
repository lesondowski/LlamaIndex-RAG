"""Integration Test cho luồng Incremental Update / Re-index.

Chạy test: python -m pytest app/tests/test_incremental_update.py -v
"""
import io  # Bắt buộc phải có để dùng BytesIO
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from llama_index.core import Document

from app.main import app
from app.config import UPLOAD_DIR
from app.rag.indexing import vector_store as vs
from app.rag.types import FILE_ID

# Khởi tạo TestClient
client = TestClient(app)

# ID giả lập để test
TEST_FILE_ID = "1234567890abcdef1234567890abcdef"
TEST_PDF_PATH = UPLOAD_DIR / f"{TEST_FILE_ID}.pdf"
TEST_META_PATH = UPLOAD_DIR / f"{TEST_FILE_ID}.json"

@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Dọn dẹp dữ liệu trước và sau khi test."""
    # Setup: Tạo một file PDF rỗng (chỉ cần có 5 byte magic header)
    TEST_PDF_PATH.write_bytes(b"%PDF-1.4\n%...")
    
    # Xóa dữ liệu cũ nếu có trong Chroma
    vs.delete_by_file_id(TEST_FILE_ID)
    
    yield
    
    # Teardown: Xóa file vật lý và DB
    TEST_PDF_PATH.unlink(missing_ok=True)
    TEST_META_PATH.unlink(missing_ok=True)
    vs.delete_by_file_id(TEST_FILE_ID)

# Giả lập LlamaParse trả về 2 documents
@patch("app.rag.ingestion.loader.PDFLoader.load_llama_parse")
def test_full_incremental_update_flow(mock_load_llama_parse):
    # 1. GIẢ LẬP PARSE DATA
    mock_load_llama_parse.return_value = [
        Document(text="Nội dung trang 1 về Toán", metadata={"page": 1}),
        Document(text="Nội dung trang 2 về Lý", metadata={"page": 2}),
    ]

    # Đọc file vào RAM để không khóa file trên ổ cứng (tránh lỗi WinError 32)
    file_bytes = TEST_PDF_PATH.read_bytes()

    # ---------------------------------------------------------
    # KỊCH BẢN 1: UPLOAD MỚI (Tạo gốc)
    # ---------------------------------------------------------
    res = client.post(
        "/upload",
        files={"file": ("test.pdf", io.BytesIO(file_bytes), "application/pdf")},
        data={
            "override_file_id": TEST_FILE_ID,
            "subject": "toan", 
            "grade": 10
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["chunks"] == 2
    assert data["subject"] == "toan"
    
    # Kiểm tra ChromaDB
    col = vs.get_collection()
    assert len(col.get(where={FILE_ID: TEST_FILE_ID}, include=[])["ids"]) == 2

    # ---------------------------------------------------------
    # KỊCH BẢN 2: PATCH METADATA (Không Re-index)
    # ---------------------------------------------------------
    start_time = time.time()
    res_patch = client.patch(
        f"/files/{TEST_FILE_ID}/metadata",
        json={"subject": "vat_ly", "grade": 11}
    )
    patch_duration = time.time() - start_time
    
    assert res_patch.status_code == 200
    assert res_patch.json()["updated_chunks"] == 2
    assert patch_duration < 0.5, "Patch metadata tốn quá nhiều thời gian (có thể đã gọi Embedding)"
    
    # Kiểm tra file JSON (Sidecar) đã cập nhật
    meta = json.loads(TEST_META_PATH.read_text(encoding="utf-8"))
    assert meta["subject"] == "vat_ly"
    assert meta["grade"] == 11
    
    # Kiểm tra ChromaDB đã cập nhật metadata
    metas = col.get(where={FILE_ID: TEST_FILE_ID}, include=["metadatas"])["metadatas"]
    for m in metas:
        assert m["subject"] == "vat_ly"
        assert m["grade"] == 11

    # ---------------------------------------------------------
    # KỊCH BẢN 3: UPLOAD GHI ĐÈ (Re-index)
    # ---------------------------------------------------------
    mock_load_llama_parse.return_value = [
        Document(text="Trang 1 mới", metadata={"page": 1}),
        Document(text="Trang 2 mới", metadata={"page": 2}),
        Document(text="Trang 3 mới", metadata={"page": 3}),
    ]
    
    res_override = client.post(
        "/upload",
        files={"file": ("test_v2.pdf", io.BytesIO(file_bytes), "application/pdf")},
        data={
            "override_file_id": TEST_FILE_ID,
            "subject": "hoa_hoc", 
            "grade": 12
        }
    )
    
    assert res_override.status_code == 200
    assert res_override.json()["chunks"] == 3
    assert res_override.json()["file_id"] == TEST_FILE_ID
    
    # KIỂM TRA TÍNH IDEMPOTENT: Số chunk trong Chroma phải là 3
    chunk_ids = col.get(where={FILE_ID: TEST_FILE_ID}, include=[])["ids"]
    assert len(chunk_ids) == 3, "Lỗi Idempotent: Chunk cũ chưa bị xóa!"
    
    # Metadata phải được cập nhật thành mới
    metas = col.get(where={FILE_ID: TEST_FILE_ID}, include=["metadatas"])["metadatas"]
    assert all(m["subject"] == "hoa_hoc" for m in metas)

    # ---------------------------------------------------------
    # KỊCH BẢN 4: LỖI EDGE CASES
    # ---------------------------------------------------------
    # 1. Patch metadata với môn học không hợp lệ
    res_bad = client.patch(
        f"/files/{TEST_FILE_ID}/metadata",
        json={"subject": "mon_hoc_rac"}
    )
    assert res_bad.status_code == 422

    # 2. Override file với ID sai định dạng
    res_bad_id = client.post(
        "/upload",
        files={"file": ("test.pdf", io.BytesIO(file_bytes), "application/pdf")},
        data={"override_file_id": "id_sai_123"}
    )
    assert res_bad_id.status_code == 400