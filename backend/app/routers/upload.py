"""Route upload: nhận PDF, validate, lưu trữ, index kèm metadata.

Luồng:
  1. Validate đuôi file (.pdf) và magic bytes (%PDF-).
  2. Kiểm & chuẩn hóa nhãn sách (subject/grade/series/volume).
  3. Stream ghi .part (giới hạn 50 MB), ghi metadata (.json.tmp), và rename atomic.
  4. Index vào Chroma qua threadpool (hỗ trợ ghi đè nếu truyền override_file_id).
  5. Cleanup mọi file dở khi lỗi.

Endpoints:
  POST   /upload                     — upload + index (hỗ trợ ghi đè)
  PATCH  /files/{file_id}/metadata   — cập nhật tag (không cần embed lại)
  GET    /metadata/options           — whitelist cho frontend
  GET    /files                      — liệt kê tài liệu
  DELETE /files/{file_id}            — xóa tài liệu + vector
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import MAX_FILE_SIZE, UPLOAD_DIR
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from app.rag.indexing.index_manager import IndexingError, get_index_manager
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE

from app.rag.types import (
    FILE_ID_RE,
    GRADE, GRADE_MAX, GRADE_MIN,
    SERIES, SUBJECT, SUBJECTS,
    VOLUME, VOLUME_MAX, VOLUME_MIN,
    DocumentTags, MetadataError,
)

log = logging.getLogger(__name__)
router = APIRouter()

# 1 MB/chunk: RAM cố định bất kể file lớn bao nhiêu.
READ_CHUNK_BYTES = 1024 * 1024


# ----------------------------------------------------------------------------
# Đường dẫn & Metadata Helpers
# ----------------------------------------------------------------------------
def _pdf_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.json"


def _meta_tmp_path(file_id: str) -> Path:
    return _meta_path(file_id).with_suffix(".json.tmp")


def _write_meta(file_id: str, metadata: dict) -> None:
    """Ghi metadata atomic: .json.tmp → rename.
    
    Đảm bảo file .json luôn ở trạng thái hoàn chỉnh, không bị đọc dở 
    nếu GET /files chạy xen giữa lúc ghi.
    """
    target = _meta_path(file_id)
    tmp = _meta_tmp_path(file_id)
    tmp.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


def _read_meta(file_id: str) -> dict:
    """Đọc sidecar JSON, dùng utf-8-sig để bỏ qua BOM (nếu có)."""
    try:
        data = json.loads(
            _meta_path(file_id).read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ----------------------------------------------------------------------------
# Chunking + Ingest
# ----------------------------------------------------------------------------
def _build_nodes(documents, file_id: str, file_name: str, tags: DocumentTags | None = None):
    """Chuyển Document → Node, gắn tags để Chroma lọc được theo metadata."""
    tag_metadata = tags.to_metadata() if tags else {}
    page_documents: list[Document] = []

    for document in documents:
        text = document.text.strip()
        if not text:
            continue

        source_metadata = dict(document.metadata or {})
        page = source_metadata.get("page")
        if page is None:
            raise ValueError("LlamaParse không trả page metadata; không thể tạo citation.")
        try:
            page = int(page)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Page metadata không hợp lệ: {page!r}") from exc

        page_documents.append(
            Document(
                text=text,
                metadata={
                    "document_id": file_id,
                    "file_id": file_id,
                    "file_name": file_name,
                    "source": file_name,
                    "source_path": str(source_metadata.get("source", "")),
                    "document_type": "pdf",
                    "parser": "llamacloud",
                    "page": page,
                    **tag_metadata,
                },
            )
        )

    if not page_documents:
        return []

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )
    nodes = splitter.get_nodes_from_documents(page_documents)
    nodes = [n for n in nodes if n.text and n.text.strip()]
    for index, node in enumerate(nodes):
        node.metadata["chunk_index"] = index
    return nodes


def _ingest(pdf_path: Path, file_id: str, display_name: str, tags: DocumentTags | None = None) -> int:
    """Parse → chunk → embed → index. Trả về số chunk đã ghi."""
    from app.rag.ingestion.loader import PDFLoader

    documents = PDFLoader().load_llama_parse(
        path_pdf=str(pdf_path),
        markdown_output=None,
        document_id=file_id,
    )
    if not documents:
        raise HTTPException(422, "LlamaParse không trích xuất được nội dung từ PDF.")

    nodes = _build_nodes(documents, file_id, display_name, tags)
    if not nodes:
        raise HTTPException(422, "PDF không tạo được chunk có nội dung.")

    return get_index_manager().ingest_nodes(nodes, file_id=file_id, file_name=display_name)


def available_file_ids() -> set[str]:
    """Tập file_id còn tồn tại trên đĩa."""
    return {
        pdf.stem for pdf in UPLOAD_DIR.glob("*.pdf")
        if FILE_ID_RE.fullmatch(pdf.stem)
    }


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
class MetadataUpdateRequest(BaseModel):
    subject: str | None = None
    grade: int | None = None
    series: str | None = None
    volume: int | None = None


# ----------------------------------------------------------------------------
# POST /upload
# ----------------------------------------------------------------------------
@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    override_file_id: str | None = Form(None), # Cho phép ghi đè file cũ
    subject: str | None = Form(None),
    grade: int | None = Form(None),
    series: str | None = Form(None),
    volume: int | None = Form(None),
):
    name = file.filename or ""

    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ hỗ trợ file PDF")

    head = await file.read(5)
    if head != b"%PDF-":
        raise HTTPException(400, "File không phải PDF hợp lệ")

    try:
        tags = DocumentTags.from_input(subject, grade, series, volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None

    display_name = name.replace("\\", "/").rsplit("/", 1)[-1]

    # Xử lý logic ghi đè hoặc tạo mới
    if override_file_id:
        if not FILE_ID_RE.fullmatch(override_file_id):
            raise HTTPException(400, "override_file_id không hợp lệ (phải là 32 hex)")
        file_id = override_file_id
        log.info("Ghi đè tài liệu với file_id=%s", file_id)
    else:
        file_id = uuid.uuid4().hex

    dest = _pdf_path(file_id)
    meta_file = _meta_path(file_id)
    meta_tmp = _meta_tmp_path(file_id)
    pdf_tmp = UPLOAD_DIR / f"{file_id}.part"

    size = len(head)

    try:
        if size > MAX_FILE_SIZE:
            raise HTTPException(413, f"File vượt quá {MAX_FILE_SIZE // (1024 * 1024)} MB")

        # Ghi .part trước, rename sau → GET /files không thấy PDF dở.
        with pdf_tmp.open("wb") as out:
            out.write(head)
            while chunk := await file.read(READ_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"File vượt quá {MAX_FILE_SIZE // (1024 * 1024)} MB")
                out.write(chunk)

        # Metadata ghi trước PDF: mọi PDF trong /files đều đã có metadata.
        meta = {
            "file_id": file_id,
            "name": display_name,
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            **tags.to_metadata(),
        }
        _write_meta(file_id, meta)
        # Windows requires deleting destination file before replace
        if dest.exists():
            dest.unlink()
        pdf_tmp.replace(dest)

    except BaseException:
        for leftover in (pdf_tmp, dest, meta_file, meta_tmp):
            leftover.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    # Gọi _ingest. Nếu là ghi đè, ingest_nodes sẽ tự động dọn chunk cũ.
    try:
        chunks = await run_in_threadpool(_ingest, dest, file_id, display_name, tags)
    except BaseException as exc:
        for leftover in (dest, meta_file):
            leftover.unlink(missing_ok=True)

        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, IndexingError):
            log.error("Index thất bại cho %s: %s", display_name, exc)
            raise HTTPException(503, f"Không thể xử lý tài liệu: {exc}")
        if isinstance(exc, Exception):
            log.exception("Lỗi không lường trước khi ingest %s", display_name)
            raise HTTPException(500, "Lỗi nội bộ khi xử lý tài liệu")
        raise

    return {
        "file_id": file_id,
        "name": display_name,
        "size": size,
        "chunks": chunks,
        **tags.to_metadata(),
    }


# ----------------------------------------------------------------------------
# PATCH /files/{file_id}/metadata (Incremental Update)
# ----------------------------------------------------------------------------
@router.patch("/files/{file_id}/metadata")
def update_file_metadata(file_id: str, req: MetadataUpdateRequest):
    """Cập nhật nhãn (subject, grade...) mà không cần parse lại PDF."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ")
        
    try:
        tags = DocumentTags.from_input(req.subject, req.grade, req.series, req.volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None
        
    new_meta = tags.to_metadata()
    if not new_meta:
        raise HTTPException(400, "Không có trường thông tin nào để cập nhật")

    # 1. Update trong ChromaDB (Vector Store)
    try:
        n_chunks = get_index_manager().update_metadata(file_id, new_meta)
    except IndexingError as e:
        raise HTTPException(503, str(e))

    # 2. Update trong file JSON lưu trữ
    meta_file = _read_meta(file_id)
    if meta_file:
        meta_file.update(new_meta)
        _write_meta(file_id, meta_file)

    return {
        "message": "Cập nhật metadata thành công", 
        "updated_chunks": n_chunks, 
        "updated_tags": new_meta
    }


# ----------------------------------------------------------------------------
# GET /metadata/options
# ----------------------------------------------------------------------------
@router.get("/metadata/options")
def metadata_options():
    return {
        "subjects": sorted(SUBJECTS),
        "grade": {"min": GRADE_MIN, "max": GRADE_MAX},
        "volume": {"min": VOLUME_MIN, "max": VOLUME_MAX},
    }


# ----------------------------------------------------------------------------
# GET /files
# ----------------------------------------------------------------------------
@router.get("/files")
def list_files():
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


# ----------------------------------------------------------------------------
# DELETE /files/{file_id}
# ----------------------------------------------------------------------------
@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str):
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(400, "file_id không hợp lệ")

    pdf = _pdf_path(file_id)

    try:
        removed = get_index_manager().delete_by_file_id(file_id)
    except Exception as e:
        log.exception("Không xóa được vector của %s", file_id)
        raise HTTPException(503, f"Không xóa được dữ liệu index: {e}")

    if not pdf.is_file() and removed == 0:
        raise HTTPException(404, "Không tìm thấy tài liệu")

    try:
        pdf.unlink(missing_ok=True)
        _meta_path(file_id).unlink(missing_ok=True)
    except OSError as e:
        raise HTTPException(500, f"Không xóa được file: {e}")