"""Route upload: nhận PDF, validate, lưu trữ, index kèm metadata.

Endpoints:
  POST   /upload                     — upload file mới & index
  POST   /files/{file_id}/reindex    — cập nhật file thông minh (Incremental)
  PATCH  /files/{file_id}/metadata   — cập nhật tag (không embed lại)
  GET    /files                      — liệt kê tài liệu
  DELETE /files/{file_id}            — xóa tài liệu + vector
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import BaseNode

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
# Helpers: File Paths & Metadata
# ============================================================================
def _pdf_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.json"


def _meta_tmp_path(file_id: str) -> Path:
    return _meta_path(file_id).with_suffix(".json.tmp")


def _write_meta(file_id: str, metadata: dict) -> None:
    """Ghi metadata atomic: .json.tmp -> rename.
    Đảm bảo file .json luôn ở trạng thái hoàn chỉnh, không bị đọc dở nếu GET /files chạy xen giữa.
    """
    target = _meta_path(file_id)
    tmp = _meta_tmp_path(file_id)
    tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def _read_meta(file_id: str) -> dict:
    """Đọc sidecar JSON, dùng utf-8-sig để bỏ qua BOM (nếu có)."""
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


async def _save_upload_file(file: UploadFile, dest_path: Path) -> int:
    """Đọc stream từ UploadFile, kiểm tra giới hạn và ghi an toàn ra đĩa.
    
    Cơ chế:
      - Ghi ra file `.part` trước, sau đó rename thành file chính thức.
      - Giới hạn `MAX_FILE_SIZE` để tránh tấn công từ chối dịch vụ (DoS).
      - Tự động dọn dẹp file `.part` nếu có lỗi xảy ra.
      
    Returns:
        Kích thước file (bytes) đã ghi thành công.
    """
    pdf_tmp = dest_path.with_suffix(".part")
    size = pdf_tmp.stat().st_size

    try:
        with pdf_tmp.open("ab") as out:
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
            raise ValueError("LlamaParse không trả page metadata.")
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
    """Chạy LlamaParse và chunking (chạy trong threadpool để không block event loop)."""
    from app.rag.ingestion.loader import PDFLoader
    documents = PDFLoader().load_llama_parse(
        path_pdf=str(pdf_path), markdown_output=None, document_id=file_id
    )
    if not documents:
        raise HTTPException(422, "LlamaParse không trích xuất được nội dung từ PDF.")
    nodes = _build_nodes(documents, file_id, display_name, tags)
    if not nodes:
        raise HTTPException(422, "PDF không tạo được chunk có nội dung.")
    return nodes


def available_file_ids() -> set[str]:
    return {pdf.stem for pdf in UPLOAD_DIR.glob("*.pdf") if FILE_ID_RE.fullmatch(pdf.stem)}


# ============================================================================
# Models
# ============================================================================
class MetadataUpdateRequest(BaseModel):
    subject: Optional[str] = None
    grade: Optional[int] = None
    series: Optional[str] = None
    volume: Optional[int] = None


# ============================================================================
# POST /upload (Full Ingest)
# ============================================================================
@router.post("/upload")
async def upload_pdf(
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
        # Đã đọc 5 bytes, cần ghi nốt 5 bytes đó trước khi ghi tiếp
        with dest.with_suffix(".part").open("wb") as out:
            out.write(head)
        size = await _save_upload_file(file, dest)
    except BaseException as exc:
        for leftover in (dest.with_suffix(".part"), dest):
            leftover.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"Lỗi khi ghi file: {exc}")

    # 2. Ghi metadata
    meta = {
        "file_id": file_id, "name": display_name, "size": size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        **tags.to_metadata(),
    }
    _write_meta(file_id, meta)

    # 3. Parse & Ingest
    try:
        nodes = await run_in_threadpool(_parse_pdf_to_nodes, dest, file_id, display_name, tags)
        chunks = await run_in_threadpool(
            get_index_manager().ingest_nodes, nodes, file_id, display_name
        )
    except BaseException as exc:
        # Rollback nếu index thất bại (xóa file PDF và JSON)
        for leftover in (dest, meta_file):
            leftover.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, IndexingError):
            log.error("Index thất bại cho %s: %s", display_name, exc, exc_info=True)
            raise HTTPException(503, f"Không thể xử lý tài liệu: {exc}")
        log.exception("Lỗi không lường trước khi ingest %s", display_name)
        raise HTTPException(500, "Lỗi nội bộ khi xử lý tài liệu.")

    log.info("Upload thành công: %s (%d chunks)", display_name, chunks)
    return {"file_id": file_id, "name": display_name, "size": size, "chunks": chunks, **tags.to_metadata()}


# ============================================================================
# POST /files/{file_id}/reindex (Incremental Update)
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
        raise HTTPException(404, "Không tìm thấy tài liệu gốc.")

    # 1. Nếu có file mới, ghi đè và cập nhật display_name
    display_name = old_meta.get("name", pdf_path.name)
    if file:
        name = file.filename or ""
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "Chỉ hỗ trợ file PDF.")
        
        head = await file.read(5)
        _validate_pdf_header(head)

        # Tạo lại file .part và ghi đè
        with pdf_path.with_suffix(".part").open("wb") as out:
            out.write(head)
        
        try:
            new_size = await _save_upload_file(file, pdf_path)
            old_meta["size"] = new_size
        except BaseException as exc:
            pdf_path.with_suffix(".part").unlink(missing_ok=True)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(500, f"Lỗi khi ghi file mới: {exc}")
        
        # Cập nhật tên hiển thị nếu user đổi tên file
        display_name = name.replace("\\", "/").rsplit("/", 1)[-1]
        old_meta["name"] = display_name

    # 2. Xử lý Metadata: Phân biệt None (giữ cũ) vs "" (xóa)
    # Dùng `is not None` để bắt được trường hợp client gửi chuỗi rỗng ""
    final_subject = subject if subject is not None else old_meta.get(SUBJECT)
    final_grade = grade if grade is not None else old_meta.get(GRADE)
    final_series = series if series is not None else old_meta.get(SERIES)
    final_volume = volume if volume is not None else old_meta.get(VOLUME)
    
    try:
        tags = DocumentTags.from_input(final_subject, final_grade, final_series, final_volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None

    # 3. Parse & Incremental Ingest
    try:
        nodes = await run_in_threadpool(_parse_pdf_to_nodes, pdf_path, file_id, display_name, tags)
        stats = await run_in_threadpool(
            get_index_manager().incremental_reindex_nodes, nodes, file_id, display_name
        )
    except IndexingError as exc:
        log.error("Incremental reindex thất bại cho %s: %s", file_id, exc, exc_info=True)
        raise HTTPException(503, f"Lỗi khi cập nhật tài liệu: {exc}")
    except Exception as exc:
        log.exception("Lỗi parse PDF khi reindex %s", file_id)
        raise HTTPException(500, f"Lỗi khi parse PDF: {exc}")

    # 4. Cập nhật Metadata JSON (Đồng bộ cả tag và xóa tag nếu cần)
    new_meta = tags.to_metadata()
    old_meta.update(new_meta)
    
    # Xóa các key không còn giá trị trong JSON để đồng bộ tuyệt đối với ChromaDB
    for key in (SUBJECT, GRADE, SERIES, VOLUME):
        if key not in new_meta:
            old_meta.pop(key, None)
            
    old_meta["file_id"] = file_id
    old_meta["name"] = display_name
    _write_meta(file_id, old_meta)

    log.info("Incremental reindex thành công: %s. Stats: %s", display_name, stats)
    return {"file_id": file_id, "message": "Cập nhật thành công", **stats}


# ============================================================================
# PATCH /files/{file_id}/metadata (Chỉ cập nhật Tag, không embed lại)
# ============================================================================
@router.patch("/files/{file_id}/metadata")
def update_file_metadata(file_id: str, req: MetadataUpdateRequest):
    """Cập nhật nhãn (subject, grade...) trực tiếp vào ChromaDB và JSON, không cần re-index."""
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

        meta = _read_meta(file_id)
        stat = pdf.stat()
        items.append({
            "file_id": file_id,
            "name": meta.get("name") or f"{file_id[:8]}.pdf",
            "size": stat.st_size,
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
        log.exception("Không xóa được vector của %s", file_id)
        raise HTTPException(503, f"Không xóa được dữ liệu index: {e}")

    if not pdf.is_file() and removed == 0:
        raise HTTPException(404, "Không tìm thấy tài liệu.")

    try:
        pdf.unlink(missing_ok=True)
        _meta_path(file_id).unlink(missing_ok=True)
    except OSError as e:
        log.error("Không xóa được file vật lý của %s: %s", file_id, e)
        raise HTTPException(500, f"Không xóa được file: {e}")