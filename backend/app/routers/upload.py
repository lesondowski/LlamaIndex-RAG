"""Route upload: nhận PDF, validate, lưu trữ, index kèm metadata.

Luồng:
  1. Kiểm đuôi file (.pdf) — lớp lọc nhanh
  2. Kiểm magic bytes (%PDF-) — lớp xác thực thật
  3. Kiểm & chuẩn hóa nhãn sách (subject/grade/series/volume)
  4. Stream ghi .part 1 MB/chunk, check size ≤ 50 MB
  5. Ghi metadata atomic (.json.tmp → rename)
  6. Rename .part → .pdf (atomic)
  7. Index vào Chroma qua threadpool
  8. Cleanup mọi file dở khi lỗi

Endpoints:
  POST   /upload              — upload + index
  GET    /metadata/options    — whitelist cho frontend
  GET    /files               — liệt kê tài liệu
  DELETE /files/{file_id}     — xóa tài liệu + vector
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import MAX_FILE_SIZE, UPLOAD_DIR
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from app.rag.indexing.index_manager import IndexingError, get_index_manager
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE

from app.rag.types import (
    FILE_ID_RE,           # dùng chung với tầng lọc — tránh định nghĩa trùng
    GRADE, GRADE_MAX, GRADE_MIN,
    SERIES, SUBJECT, SUBJECTS,
    VOLUME, VOLUME_MAX, VOLUME_MIN,
    DocumentTags, MetadataError,
)

log = logging.getLogger(__name__)
router = APIRouter()

# 1 MB/chunk: RAM cố định ~1 MB bất kể file lớn bao nhiêu.
# Đặt tên khác CHUNK_SIZE (đơn vị token, ~512) để không ghi đè biến chunk văn bản.
READ_CHUNK_BYTES = 1024 * 1024


# ----------------------------------------------------------------------------
# Đường dẫn & metadata
# ----------------------------------------------------------------------------
def _pdf_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.json"


def _meta_tmp_path(file_id: str) -> Path:
    """File tạm của metadata — cần đưa vào danh sách dọn nếu _write_meta lỗi giữa chừng."""
    return _meta_path(file_id).with_suffix(".json.tmp")


def _write_meta(file_id: str, metadata: dict) -> None:
    """Ghi metadata atomic: .json.tmp → rename.

    GET /files có thể chạy xen giữa lúc ghi → đọc JSON dở. Rename là syscall
    atomic nên file .json luôn ở trạng thái hoàn chỉnh hoặc chưa tồn tại.
    """
    target = _meta_path(file_id)
    tmp = _meta_tmp_path(file_id)
    tmp.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


def _read_meta(file_id: str) -> dict:
    """Trả {} nếu file thiếu hoặc hỏng — không làm vỡ danh sách /files."""
    try:
        data = json.loads(_meta_path(file_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ----------------------------------------------------------------------------
# Chunking + ingest
# ----------------------------------------------------------------------------
def _build_nodes(documents, file_id: str, file_name: str, tags: DocumentTags | None = None):
    """Chuyển Document → Node, gắn tags để Chroma lọc được theo metadata sau này."""
    tag_metadata = tags.to_metadata() if tags else {}
    page_documents: list[Document] = []

    for document in documents:
        text = document.text.strip()
        if not text:
            continue

        source_metadata = dict(document.metadata or {})
        page = source_metadata.get("page")
        if page is None:
            raise ValueError(
                "LlamaParse không trả page metadata; không thể tạo citation chính xác."
            )
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
                    **tag_metadata,   # chỉ khóa có giá trị
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
    """Parse → chunk → embed → index. Trả về số chunk đã ghi vào Chroma."""
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
    """Tập file_id còn tồn tại trên đĩa — tầng chat dùng để loại file đã xóa."""
    return {
        pdf.stem for pdf in UPLOAD_DIR.glob("*.pdf")
        if FILE_ID_RE.fullmatch(pdf.stem)
    }


# ----------------------------------------------------------------------------
# POST /upload
# ----------------------------------------------------------------------------
@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    subject: str | None = Form(None),
    grade: int | None = Form(None),
    series: str | None = Form(None),
    volume: int | None = Form(None),
):
    name = file.filename or ""

    # Lớp 1 — đuôi file. Nhanh, dễ giả mạo, chỉ để lọc sớm.
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ hỗ trợ file PDF")

    # Lớp 2 — magic bytes. Đọc 5 byte đầu; PDF hợp lệ luôn bắt đầu bằng "%PDF-".
    head = await file.read(5)
    if head != b"%PDF-":
        raise HTTPException(400, "File không phải PDF hợp lệ")

    # Lớp 3 — nhãn sách. Validate TRƯỚC khi ghi file để từ chối sớm, không tốn I/O.
    try:
        tags = DocumentTags.from_input(subject, grade, series, volume)
    except MetadataError as exc:
        raise HTTPException(422, str(exc)) from None

    # display_name chỉ để hiển thị — bỏ mọi thư mục client gửi kèm.
    # Không bao giờ dùng để ghép đường dẫn trên đĩa.
    display_name = name.replace("\\", "/").rsplit("/", 1)[-1]

    # file_id ngẫu nhiên: tránh ghi đè khi trùng tên, tránh path traversal.
    file_id = uuid.uuid4().hex
    dest = _pdf_path(file_id)
    meta_file = _meta_path(file_id)
    meta_tmp = _meta_tmp_path(file_id)
    pdf_tmp = UPLOAD_DIR / f"{file_id}.part"

    size = len(head)

    try:
        if size > MAX_FILE_SIZE:
            raise HTTPException(413, f"File vượt quá {MAX_FILE_SIZE // (1024 * 1024)} MB")

        # Ghi .part trước, rename sau → GET /files không bao giờ thấy PDF dở.
        with pdf_tmp.open("wb") as out:
            out.write(head)
            while chunk := await file.read(READ_CHUNK_BYTES):
                size += len(chunk)
                # Cắt ngay khi vượt giới hạn — không để file chiếm hết đĩa.
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"File vượt quá {MAX_FILE_SIZE // (1024 * 1024)} MB")
                out.write(chunk)

        # Metadata ghi trước PDF: mọi PDF xuất hiện trong /files đều đã có metadata.
        meta = {
            "file_id": file_id,
            "name": display_name,
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            **tags.to_metadata(),
        }
        _write_meta(file_id, meta)
        pdf_tmp.replace(dest)   # rename atomic — PDF xuất hiện đầy đủ

    except BaseException:
        # Dọn mọi file dở, kể cả .json.tmp nếu _write_meta lỗi giữa chừng.
        for leftover in (pdf_tmp, dest, meta_file, meta_tmp):
            leftover.unlink(missing_ok=True)
        raise
    finally:
        # Đóng file handle chủ động, không dựa vào GC.
        await file.close()

    # Index: chỉ trả 200 khi tài liệu đã truy vấn được.
    # Chạy trong threadpool vì LlamaParse + Gemini + Chroma đều blocking.
    try:
        chunks = await run_in_threadpool(_ingest, dest, file_id, display_name, tags)
    except BaseException as exc:
        # Tài liệu không index được thì không được tồn tại — tránh PDF "ma".
        # ingest_nodes đã tự rollback vector trước khi ném IndexingError.
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
        raise   # CancelledError — client hủy giữa chừng

    return {
        "file_id": file_id,
        "name": display_name,
        "size": size,
        "chunks": chunks,
        **tags.to_metadata(),
    }


# ----------------------------------------------------------------------------
# GET /metadata/options
# ----------------------------------------------------------------------------
@router.get("/metadata/options")
def metadata_options():
    """Miền giá trị hợp lệ cho frontend dựng dropdown — tránh user gõ tự do."""
    return {
        "subjects": sorted(SUBJECTS),
        "grade": {"min": GRADE_MIN, "max": GRADE_MAX},
        "volume": {"min": VOLUME_MIN, "max": VOLUME_MAX},
        # series chỉ validate format, không whitelist — chương trình giáo dục đổi theo năm.
    }


# ----------------------------------------------------------------------------
# GET /files
# ----------------------------------------------------------------------------
@router.get("/files")
def list_files():
    """Liệt kê tài liệu, cũ nhất trước. `def` (không `async`) → FastAPI chạy trong threadpool."""
    items = []
    for pdf in UPLOAD_DIR.glob("*.pdf"):
        file_id = pdf.stem
        if not FILE_ID_RE.fullmatch(file_id):
            continue   # bỏ file lạ do user tự chép vào

        meta = _read_meta(file_id)
        stat = pdf.stat()
        items.append({
            "file_id": file_id,
            "name": meta.get("name") or f"{file_id[:8]}.pdf",
            "size": stat.st_size,   # lấy từ đĩa — luôn chính xác
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
    """Xóa vector → PDF → metadata.

    Vector trước để nếu xóa vector lỗi, PDF còn → retry được. Ngược lại sẽ để
    chunk mồ côi vẫn bị retrieve mà không còn file_id để lọc.
    """
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
        # Windows: file đang bị khóa (vd OneDrive sync) → báo lỗi rõ thay vì 500 trống.
        raise HTTPException(500, f"Không xóa được file: {e}")