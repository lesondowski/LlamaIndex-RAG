"""
Router quản lý tài liệu: tải lên, liệt kê, xóa.

Mỗi tài liệu được lưu thành hai file cạnh nhau trong UPLOAD_DIR:
  <file_id>.pdf    nội dung PDF gốc
  <file_id>.json   metadata hiển thị (tên gốc, dung lượng, thời điểm tải lên)

File metadata cần thiết vì tên file trên đĩa là file_id ngẫu nhiên — nếu không
lưu tên gốc, danh sách "Nguồn" không dựng lại được sau khi tải lại trang.

Endpoint:
  POST   /upload           nhận PDF, trả { file_id, name, size, chunks }
  GET    /files            liệt kê tài liệu đã tải lên
  DELETE /files/{file_id}  xóa tài liệu
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import MAX_FILE_SIZE, UPLOAD_DIR
from app.rag.indexing.index_manager import IndexingError, get_index_manager
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode

log = logging.getLogger(__name__)

router = APIRouter()

# Đọc theo từng khối 1 MB để RAM không bao giờ nạp cả file. Đặt tên khác CHUNK_SIZE
# vì tên đó đã bị chiếm bởi hằng số chunk văn bản import ở trên — dùng trùng tên sẽ
# ghi đè giá trị và khiến SentenceSplitter nhận nhầm kích thước chunk khổng lồ.
READ_CHUNK_BYTES = 1024 * 1024

# file_id hợp lệ là 32 ký tự hex (uuid4().hex). Kiểm tra ở mọi endpoint nhận
# file_id từ client là bắt buộc để ngăn path traversal khi ghép thành đường dẫn.
FILE_ID_RE = re.compile(r"[0-9a-f]{32}")


def _pdf_path(file_id: str):
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str):
    return UPLOAD_DIR / f"{file_id}.json"


def _build_nodes(elements, file_name: str):
    """Gom element (đầu ra PDFLoader.load) theo trang rồi cắt chunk.

    Mỗi trang là một Document riêng để chunk nào cũng biết chắc nó thuộc trang
    nào (cần cho panel trích dẫn). Dùng SentenceSplitter thay vì MarkdownNodeParser
    vì parser sau cắt ở mọi dòng '#', mà unstructured gán Title cho nhiều dòng
    thường → sinh nhiều chunk vài chục ký tự, vô dụng khi retrieval.
    """
    buckets: dict = {}
    for el in elements:
        kind = el.metadata.get("element_type")
        if kind == "Title":
            text = f"## {el.text}"
        elif kind == "ListItem":
            text = f"- {el.text}"
        elif kind == "Table":
            text = f"```table\n{el.text}\n```"
        else:
            text = el.text
        buckets.setdefault(el.metadata.get("page"), []).append(text)

    pages = []
    for page, parts in buckets.items():
        meta = {"file_name": file_name, "document_type": "pdf"}
        if page is not None:  # Chroma không nhận None trong metadata
            meta["page"] = int(page)
        pages.append(Document(text="\n\n".join(parts), metadata=meta))

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )
    nodes = [
        n for n in splitter.get_nodes_from_documents(pages)
        if n.get_content(metadata_mode=MetadataMode.NONE).strip()
    ]
    for i, node in enumerate(nodes):
        node.metadata["chunk_index"] = i
    return nodes


def _ingest(pdf_path, file_id: str, display_name: str) -> int:
    """Parse PDF → chunk → embedding → vector store. Đồng bộ và nặng, route gọi
    qua run_in_threadpool. Trả về số chunk đã index."""
    # Import lười: unstructured/pymupdf nặng, không nên làm chậm lúc khởi động.
    from app.rag.ingestion.loader import PDFLoader

    elements = PDFLoader().load(str(pdf_path))
    if not elements:
        raise HTTPException(
            status_code=422,
            detail="Không trích xuất được văn bản từ PDF. Nếu là bản scan, cần OCR "
                   "(cài Tesseract và dùng strategy='auto'/'hi_res' trong PDFLoader).",
        )
    nodes = _build_nodes(elements, display_name)
    return get_index_manager().ingest_nodes(nodes, file_id=file_id, file_name=display_name)


def _read_meta(file_id: str) -> dict:
    """Đọc metadata; trả {} nếu thiếu hoặc hỏng để không làm hỏng cả danh sách."""
    try:
        data = json.loads(_meta_path(file_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@router.post("/upload")
async def upload_pdf(
    # Tên tham số phải trùng key trong fd.append('file', ...) ở api.js,
    # nếu khác FastAPI sẽ trả 422.
    file: UploadFile = File(...),
):
    # `or ""` xử lý trường hợp filename là None.
    name = file.filename or ""

    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file PDF")

    # Magic bytes: mọi file PDF hợp lệ đều bắt đầu bằng "%PDF-", không phụ thuộc
    # vào tên file hay Content-Type do client tự khai báo.
    head = await file.read(5)
    if head != b"%PDF-":
        raise HTTPException(status_code=400, detail="File không phải PDF hợp lệ")

    # Chỉ lấy phần tên file, bỏ mọi thư mục nếu client gửi cả đường dẫn. Giá trị
    # này chỉ dùng để hiển thị, không bao giờ được dùng để ghép đường dẫn trên đĩa.
    display_name = name.replace("\\", "/").rsplit("/", 1)[-1]

    # file_id ngẫu nhiên làm định danh và tên file trên đĩa. Không dùng tên gốc
    # vì hai người có thể upload file trùng tên (ghi đè nhau) và tên do client
    # kiểm soát có thể chứa "../" (path traversal).
    file_id = uuid.uuid4().hex
    dest = _pdf_path(file_id)
    meta_file = _meta_path(file_id)

    # Ghi vào ".part" rồi đổi tên ở cuối: GET /files không bao giờ thấy PDF ghi dở.
    tmp = UPLOAD_DIR / f"{file_id}.part"
    size = len(head)

    try:
        with tmp.open("wb") as out:
            out.write(head)  # ghi lại 5 byte đã đọc ở bước kiểm tra magic bytes

            while chunk := await file.read(READ_CHUNK_BYTES):
                size += len(chunk)

                # Kiểm tra trong lúc đọc để cắt ngay request quá khổ thay vì để
                # nó chiếm hết đĩa rồi mới từ chối.
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File vượt quá 50MB")

                out.write(chunk)

        # Ghi metadata trước khi "công bố" PDF, để bất kỳ PDF nào hiện ra trong
        # danh sách đều đã có đủ thông tin.
        meta = {
            "file_id": file_id,
            "name": display_name,
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        # replace() là đổi tên nguyên tử: từ đây PDF xuất hiện đầy đủ.
        tmp.replace(dest)

    except BaseException:
        # Dùng BaseException (không phải Exception) vì khi client ngắt kết nối,
        # asyncio ném CancelledError — vốn kế thừa BaseException.
        for leftover in (tmp, dest, meta_file):
            leftover.unlink(missing_ok=True)
        raise

    try:
        chunks = await run_in_threadpool(_ingest, dest, file_id, display_name)
    except BaseException as exc:
        # Tài liệu không index được thì không được tồn tại: tránh PDF "ma" trong
        # danh sách Nguồn nhưng không bao giờ trả lời được. ingest_nodes đã tự
        # rollback vector.
        for leftover in (dest, meta_file):
            leftover.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, IndexingError):
            log.error("Index thất bại cho %s: %s", display_name, exc)
            raise HTTPException(status_code=503, detail=f"Không thể xử lý tài liệu: {exc}")
        if isinstance(exc, Exception):
            log.exception("Lỗi không lường trước khi ingest %s", display_name)
            raise HTTPException(status_code=500, detail="Lỗi nội bộ khi xử lý tài liệu")
        raise  # CancelledError

    return {"file_id": file_id, "name": display_name, "size": size, "chunks": chunks}


@router.get("/files")
def list_files():
    """Liệt kê tài liệu đã tải lên, cũ nhất trước.

    Khai báo bằng def (không phải async def) để FastAPI chạy trong threadpool —
    hàm đọc đĩa đồng bộ, không nên chặn event loop.
    """
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
        })

    # Chuỗi ISO-8601 cùng múi giờ UTC nên so sánh chuỗi cũng đúng thứ tự thời gian.
    items.sort(key=lambda item: item["uploaded_at"])
    return {"files": items}


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str):
    """Xóa một tài liệu (PDF và metadata)."""
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(status_code=400, detail="file_id không hợp lệ")

    pdf = _pdf_path(file_id)

    # Xóa vector trước: nếu bước này lỗi, PDF vẫn còn nên người dùng thử xóa lại
    # được. Làm ngược lại sẽ để chunk mồ côi vẫn bị truy xuất mà không còn cách xóa.
    try:
        removed = get_index_manager().delete_by_file_id(file_id)
    except Exception as e:
        log.exception("Không xóa được vector của %s", file_id)
        raise HTTPException(status_code=503, detail=f"Không xóa được dữ liệu index: {e}")
    if not pdf.is_file() and removed == 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

    try:
        pdf.unlink(missing_ok=True)
        _meta_path(file_id).unlink(missing_ok=True)
    except OSError as e:
        # Trên Windows, file đang bị chương trình khác khóa (vd OneDrive đang đồng
        # bộ) không xóa được: báo lỗi rõ ràng thay vì để server trả 500 trống.
        raise HTTPException(status_code=500, detail=f"Không xóa được file: {e}")