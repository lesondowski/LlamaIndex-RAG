"""Route upload: nhận PDF, validate, lưu trữ, index kèm metadata.

Endpoints:
  POST   /upload                     — upload file mới & index (chạy ngầm)
  POST   /files/{file_id}/reindex    — cập nhật file thông minh (Incremental)
  PATCH  /files/{file_id}/metadata   — cập nhật tag (không embed lại)
  GET    /files                      — liệt kê tài liệu
  GET    /files/{file_id}/status     — lấy trạng thái xử lý (Polling)
  DELETE /files/{file_id}            — xóa tài liệu + vector
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pymupdf
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode
from pydantic import BaseModel

from app.config import MAX_FILE_SIZE, UPLOAD_DIR
from app.rag.indexing.index_manager import IndexingError, get_index_manager
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE
from app.rag.types import (
    FILE_ID_RE, GRADE, SERIES, SUBJECT, VOLUME,
    DocumentTags, MetadataError,
)

log = logging.getLogger(__name__)
router = APIRouter()
READ_CHUNK_BYTES = 1024 * 1024  # 1 MB

# ============================================================================
# LƯU TRỮ TRẠNG THÁI TẠM THỜI (IN-MEMORY STATE)
# ============================================================================
# Cấu trúc: { file_id: {"status": "processing", "progress": 10, "message": "..."} }
# LƯU Ý: Trong môi trường Production thực tế, nên dùng Redis hoặc Database
# thay vì lưu trong RAM để đảm bảo không mất dữ liệu khi server restart.
UPLOAD_PROGRESS = {}


# ============================================================================
# Helpers: File Paths & Metadata
# ============================================================================
def _pdf_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.pdf"

def _meta_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.json"

def _meta_tmp_path(file_id: str) -> Path:
    return _meta_path(file_id).with_suffix(".json.tmp")

def _write_meta(file_id: str, metadata: dict) -> None:
    """Ghi metadata atomic: .json.tmp -> rename."""
    target = _meta_path(file_id)
    tmp = _meta_tmp_path(file_id)
    tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)

def _read_meta(file_id: str) -> dict:
    """Đọc sidecar JSON, dùng utf-8-sig để bỏ qua BOM."""
    try:
        data = json.loads(_meta_path(file_id).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ============================================================================
# Helpers: File I/O & Validation
# ============================================================================
def _validate_pdf_header(head: bytes) -> None:
    """Kiểm tra magic bytes: '%PDF-'."""
    if head != b"%PDF-":
        raise HTTPException(400, "File không phải PDF hợp lệ.")

def _validate_pdf_structure(pdf_path: Path) -> None:
    """Kiểm tra file PDF có cấu trúc hợp lệ và có ít nhất 1 trang."""
    try:
        doc = pymupdf.open(str(pdf_path))
        if doc.page_count == 0:
            doc.close()
            raise HTTPException(400, "File PDF không có trang nào (File rỗng).")
        doc.close()
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"Lỗi validate PDF {pdf_path.name}: {e}")
        raise HTTPException(400, f"File PDF bị lỗi cấu trúc hoặc không đúng định dạng: {e}")

async def _save_upload_file(file: UploadFile, dest_path: Path, head: bytes) -> int:
    """Đọc stream từ UploadFile, ghi an toàn ra đĩa (bao gồm cả 5 bytes header)."""
    pdf_tmp = dest_path.with_suffix(".part")
<<<<<<< HEAD
    size = pdf_tmp.stat().st_size

    try:
        with pdf_tmp.open("ab") as out:
=======
    size = len(head)

    try:
        with pdf_tmp.open("wb") as out:
            out.write(head)
>>>>>>> origin/hnnnhu
            while chunk := await file.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"File vượt quá {MAX_FILE_SIZE // (1024 * 1024)} MB.")
                out.write(chunk)

        if dest_path.exists():
            dest_path.unlink()
        pdf_tmp.replace(dest_path)
        return size
    except BaseException:
        pdf_tmp.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


# ============================================================================
# Helpers: Chunking & Ingest
# ============================================================================
def _build_nodes(
    documents: list[Document], file_id: str, file_name: str, tags: DocumentTags | None = None
) -> list[BaseNode]:
    """Chuyển Document -> Node, gắn tags để Chroma lọc được theo metadata."""
    tag_metadata = tags.to_metadata() if tags else {}
    page_documents: list[Document] = []

    for document in documents:
        text = document.text.strip()
        if not text:
            continue

        source_metadata = dict(document.metadata or {})
        page = source_metadata.get("page")
        if page is None:
            raise ValueError("Không trích xuất được metadata trang (page).")
        try:
            page = int(page)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Page không hợp lệ: {page!r}") from exc

        page_documents.append(
            Document(
                text=text,
                metadata={
                    "document_id": file_id, "file_id": file_id,
                    "file_name": file_name, "source": file_name,
                    "source_path": str(source_metadata.get("source", "")),
                    "document_type": "pdf", "parser": "llamacloud", "page": page,
                    **tag_metadata,
                },
            )
        )

    if not page_documents:
        return []

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, paragraph_separator="\n\n"
    )
    nodes = splitter.get_nodes_from_documents(page_documents)
    nodes = [n for n in nodes if n.text and n.text.strip()]
    for index, node in enumerate(nodes):
        node.metadata["chunk_index"] = index
    return nodes

def _parse_pdf_to_nodes(
    pdf_path: Path, file_id: str, display_name: str, tags: DocumentTags | None = None
) -> list[BaseNode]:
    """Try LlamaParse first, fallback to unstructured partition PDF."""
    from app.rag.ingestion.loader import PDFLoader
    loader = PDFLoader()
    documents = []
    
    try:
        log.info(f"Đang parse bằng LlamaParse: {display_name}")
        documents = loader.load_llama_parse(
            path_pdf=str(pdf_path), markdown_output=None, document_id=file_id
        )
    except Exception as e:
        log.warning(f"LlamaParse failed: {e}. Chuyển sang fallback unstructured.")
        
    if not documents:
        try:
            log.info(f"Đang parse bằng Unstructured: {display_name}")
            documents = loader.load_partition_pdf(
                file_path=str(pdf_path), document_id=file_id
            )
        except Exception as e:
            raise HTTPException(422, f"LlamaParse và unstructured đều thất bại: {e}")
            
    if not documents:
        raise HTTPException(422, "Không thể trích xuất nội dung từ PDF (LlamaParse & Unstructured đều thất bại).")
        
    nodes = _build_nodes(documents, file_id, display_name, tags)
    if not nodes:
        raise HTTPException(422, "PDF không tạo được chunk có nội dung.")
    return nodes

def available_file_ids() -> set[str]:
    return {pdf.stem for pdf in UPLOAD_DIR.glob("*.pdf") if FILE_ID_RE.fullmatch(pdf.stem)}


# ============================================================================
# HÀM CHẠY NGẦM (BACKGROUND TASK)
# ============================================================================
def _process_pdf_background(
    pdf_path: Path, file_id: str, display_name: str, tags: DocumentTags | None, meta_file: Path
):
    try:
        # 1. Parse PDF
        UPLOAD_PROGRESS[file_id] = {"status": "processing", "progress": 10, "message": "Đang đọc và phân tích tài liệu..."}
        log.info(f"[Background] Bắt đầu parse PDF cho {file_id}")
        
        nodes = _parse_pdf_to_nodes(pdf_path, file_id, display_name, tags)
        
        # 2. Chunking & Embedding
        # Thay đổi message ở đây để thân thiện với người dùng
        UPLOAD_PROGRESS[file_id] = {"status": "processing", "progress": 50, "message": "Đang cấu trúc lại dữ liệu..."}
        # Terminal vẫn log chi tiết kỹ thuật
        log.info(f"[Background] Đã chunk xong {len(nodes)} nodes. Bắt đầu đẩy vào ChromaDB.")
        
        chunks = get_index_manager().ingest_nodes(nodes, file_id, display_name)
        
        # 3. Hoàn tất
        UPLOAD_PROGRESS[file_id] = {"status": "indexed", "progress": 100, "message": "Sẵn sàng!"}
        log.info(f"[Background] Hoàn tất xử lý {display_name}: {chunks} chunks.")
    except Exception as exc:
        log.exception(f"[Background] Lỗi xử lý file {file_id}: {exc}")
        UPLOAD_PROGRESS[file_id] = {"status": "error", "progress": 100, "message": str(exc)}
        
        # Rollback: Xóa file PDF và JSON nếu xử lý thất bại
        pdf_path.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)


# ============================================================================
# Models
# ============================================================================
class MetadataUpdateRequest(BaseModel):
    subject: Optional[str] = None
    grade: Optional[int] = None
    series: Optional[str] = None
    volume: Optional[int] = None


# ============================================================================
# POST /upload (Bất đồng bộ + BackgroundTasks)
# ============================================================================
@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks, # <-- Inject BackgroundTasks
    file: UploadFile = File(...),
    override_file_id: Optional[str] = Form(None),
    subject: Optional[str] = Form(None),
    grade: Optional[int] = Form(None),
    series: Optional[str] = Form(None),
    volume: Optional[int] = Form(None),
):
    name = file.filename or ""
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ hỗ trợ file PDF.")

    head = await file.read(5)
    _validate_pdf_header(head)

    try:
        tags = DocumentTags.from_input(subject, grade, series, volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None

    display_name = name.replace("\\", "/").rsplit("/", 1)[-1]

    if override_file_id:
        if not FILE_ID_RE.fullmatch(override_file_id):
            raise HTTPException(400, "override_file_id không hợp lệ.")
        file_id = override_file_id
    else:
        file_id = uuid.uuid4().hex

    dest = _pdf_path(file_id)
    meta_file = _meta_path(file_id)

    # 1. Ghi file an toàn
    try:
<<<<<<< HEAD
        # Đã đọc 5 bytes, cần ghi nốt 5 bytes đó trước khi ghi tiếp
        with dest.with_suffix(".part").open("wb") as out:
            out.write(head)
        size = await _save_upload_file(file, dest)
=======
        size = await _save_upload_file(file, dest, head)
>>>>>>> origin/hnnnhu
    except BaseException as exc:
        dest.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"Lỗi khi ghi file: {exc}")

    # 2. Validate PDF cấu trúc
    try:
        _validate_pdf_structure(dest)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    # 3. Ghi metadata sơ bộ
    meta = {
        "file_id": file_id, "name": display_name, "size": size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        **tags.to_metadata(),
    }
    _write_meta(file_id, meta)

    # 4. Khởi tạo trạng thái ban đầu và đẩy vào Background Tasks
    UPLOAD_PROGRESS[file_id] = {"status": "processing", "progress": 5, "message": "Đang chờ xử lý..."}
    background_tasks.add_task(_process_pdf_background, dest, file_id, display_name, tags, meta_file)

    # Trả về ngay lập tức cho Frontend (không bắt Frontend chờ đợi)
    log.info(f"Đã nhận file {display_name} (ID: {file_id}). Đang xử lý ngầm...")
    return {"file_id": file_id, "status": "processing", "message": "Đã nhận file, đang xử lý."}


# ============================================================================
# GET /files/{file_id}/status (Polling Endpoint)
# ============================================================================
@router.get("/files/{file_id}/status")
def get_file_status(file_id: str):
    """Trả về tiến trình xử lý file hiện tại."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ.")
        
    status = UPLOAD_PROGRESS.get(file_id)
    if not status:
        # Nếu server bị restart (mất RAM), nhưng file đã có trên đĩa -> coi như đã index xong
        if _pdf_path(file_id).is_file():
            return {"status": "indexed", "progress": 100, "message": "Sẵn sàng"}
        raise HTTPException(404, "Không tìm thấy tiến trình xử lý.")
        
    return status


# ============================================================================
# POST /files/{file_id}/reindex (Đồng bộ - Giữ nguyên logic an toàn)
# ============================================================================
@router.post("/files/{file_id}/reindex")
async def incremental_reindex(
    file_id: str,
    file: Optional[UploadFile] = File(None),
    subject: Optional[str] = Form(None),
    grade: Optional[int] = Form(None),
    series: Optional[str] = Form(None),
    volume: Optional[int] = Form(None),
):
    """Cập nhật tài liệu (Incremental Re-index). Chỉ embed các chunk thay đổi."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ.")

    pdf_path = _pdf_path(file_id)
    old_meta = _read_meta(file_id)
    
    if not pdf_path.is_file():
        raise HTTPException(404, "Không tìm thấy tài liệu gốc để cập nhật.")

    display_name = old_meta.get("name", pdf_path.name)
    temp_pdf = pdf_path.with_suffix(".pdf.tmp")
    
    if file:
        name = file.filename or ""
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "Chỉ hỗ trợ file PDF.")
        
        head = await file.read(5)
        _validate_pdf_header(head)

        try:
<<<<<<< HEAD
            new_size = await _save_upload_file(file, pdf_path)
=======
            new_size = await _save_upload_file(file, temp_pdf, head)
>>>>>>> origin/hnnnhu
            old_meta["size"] = new_size
        except BaseException as exc:
            temp_pdf.unlink(missing_ok=True)
            if isinstance(exc, HTTPException): raise
            raise HTTPException(500, f"Lỗi khi ghi file mới: {exc}")
        
        try:
            _validate_pdf_structure(temp_pdf)
        except HTTPException:
            temp_pdf.unlink(missing_ok=True)
            raise

        display_name = name.replace("\\", "/").rsplit("/", 1)[-1]
        old_meta["name"] = display_name

    final_subject = subject if subject is not None else old_meta.get(SUBJECT)
    final_grade = grade if grade is not None else old_meta.get(GRADE)
    final_series = series if series is not None else old_meta.get(SERIES)
    final_volume = volume if volume is not None else old_meta.get(VOLUME)
    
    try:
        tags = DocumentTags.from_input(final_subject, final_grade, final_series, final_volume)
    except MetadataError as exc:
        if file: temp_pdf.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from None

    target_pdf = temp_pdf if file else pdf_path
    
    try:
        nodes = await run_in_threadpool(_parse_pdf_to_nodes, target_pdf, file_id, display_name, tags)
        
        if file:
            if pdf_path.exists(): pdf_path.unlink()
            temp_pdf.replace(pdf_path)
            
        stats = await run_in_threadpool(
            get_index_manager().incremental_reindex_nodes, nodes, file_id, display_name
        )
    except IndexingError as exc:
        if file: temp_pdf.unlink(missing_ok=True)
        log.error("Incremental reindex thất bại cho %s: %s", file_id, exc, exc_info=True)
        raise HTTPException(503, f"Lỗi khi cập nhật vector: {exc}")
    except Exception as exc:
        if file: temp_pdf.unlink(missing_ok=True)
        log.exception("Lỗi parse PDF khi reindex %s", file_id)
        raise HTTPException(422, f"File mới bị lỗi khi parse, file cũ được giữ nguyên: {exc}")

    new_meta = tags.to_metadata()
    old_meta.update(new_meta)
    
    for key in (SUBJECT, GRADE, SERIES, VOLUME):
        if key not in new_meta:
            old_meta.pop(key, None)
            
    old_meta["file_id"] = file_id
    old_meta["name"] = display_name
    _write_meta(file_id, old_meta)

    log.info("Incremental reindex thành công: %s. Stats: %s", display_name, stats)
    return {"file_id": file_id, "message": "Cập nhật thành công", **stats}


# ============================================================================
# PATCH /files/{file_id}/metadata
# ============================================================================
@router.patch("/files/{file_id}/metadata")
def update_file_metadata(file_id: str, req: MetadataUpdateRequest):
    """Cập nhật nhãn trực tiếp vào ChromaDB và JSON, không cần re-index."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ.")
    
    try:
        tags = DocumentTags.from_input(req.subject, req.grade, req.series, req.volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None
        
    new_meta = tags.to_metadata()
    if not new_meta:
        raise HTTPException(400, "Không có trường thông tin nào để cập nhật.")

    try:
        n_chunks = get_index_manager().update_metadata(file_id, new_meta)
    except IndexingError as e:
        log.error("Lỗi cập nhật metadata cho %s: %s", file_id, e, exc_info=True)
        raise HTTPException(503, str(e))

    meta_file = _read_meta(file_id)
    if meta_file:
        meta_file.update(new_meta)
        _write_meta(file_id, meta_file)

    return {"message": "Cập nhật thành công", "updated_chunks": n_chunks, "updated_tags": new_meta}


# ============================================================================
# GET /files & DELETE /files/{file_id}
# ============================================================================
@router.get("/files")
def list_files():
    """Liệt kê tất cả tài liệu hiện có trên đĩa, kèm metadata."""
    items = []
    for pdf in UPLOAD_DIR.glob("*.pdf"):
        file_id = pdf.stem
        if not FILE_ID_RE.fullmatch(file_id):
            continue

        # Ưu tiên lấy trạng thái thời gian thực từ RAM, nếu không có thì coi là 'indexed'
        current_status = UPLOAD_PROGRESS.get(file_id, {}).get("status", "indexed")

        meta = _read_meta(file_id)
        stat = pdf.stat()
        items.append({
            "file_id": file_id,
            "name": meta.get("name") or f"{file_id[:8]}.pdf",
            "size": stat.st_size,
            "status": current_status,
            "uploaded_at": meta.get("uploaded_at")
                           or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            **{key: meta[key] for key in (SUBJECT, GRADE, SERIES, VOLUME) if key in meta},
        })
    
    items.sort(key=lambda item: item["uploaded_at"])
    return {"files": items}

@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str):
    """Xóa tài liệu khỏi ổ đĩa (PDF, JSON) và xóa vector trong ChromaDB."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ.")

    pdf = _pdf_path(file_id)

    try:
        removed = get_index_manager().delete_by_file_id(file_id)
    except Exception as e:
        log.exception("Không xóa được vector của %s, sẽ tiếp tục xóa file vật lý", file_id)
        removed = 0 

    if not pdf.is_file() and removed == 0:
        raise HTTPException(404, "Không tìm thấy tài liệu.")

    try:
        pdf.unlink(missing_ok=True)
        _meta_path(file_id).unlink(missing_ok=True)
        pdf.with_suffix(".pdf.tmp").unlink(missing_ok=True)
        # Dọn dẹp luôn trạng thái trong RAM nếu có
        UPLOAD_PROGRESS.pop(file_id, None)
    except OSError as e:
        log.error("Không xóa được file vật lý của %s: %s", file_id, e)
        raise HTTPException(500, f"Không xóa được file: {e}")