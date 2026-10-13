### Viết lại api, ở chunker.py anh sẽ bỏ các parameter


import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import MAX_FILE_SIZE, UPLOAD_DIR
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode

from app.rag.indexing.index_manager import IndexingError, get_index_manager
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE   # chỉ đọc hằng số, không sửa chunker.py

log = logging.getLogger(__name__)

# APIRouter gom các endpoint liên quan; main.py gắn router này vào app chính.
router = APIRouter()

# Đọc theo từng khối 1 MB: RAM luôn ở mức ~1 MB thay vì nạp cả file vào bộ nhớ.
# ĐẶT TÊN KHÁC CHUNK_SIZE: tên đó đã bị chiếm bởi hằng số chunk văn bản import ở trên
# (app.rag.ingestion.chunker.CHUNK_SIZE, ~512 token). Dùng trùng tên từng khiến dòng này
# ghi đè luôn giá trị đó — SentenceSplitter ở _build_nodes() nhận nhầm 1 048 576 làm
# chunk_size, nghĩa là gần như không bao giờ cắt chunk (mỗi tài liệu dồn thành 1 chunk
# khổng lồ). Đổi tên để 2 khái niệm — "cỡ buffer đọc file" và "cỡ 1 chunk văn bản" —
# không bao giờ còn chung 1 biến.
READ_CHUNK_BYTES = 1024 * 1024

# file_id hợp lệ là đúng 32 ký tự hex thường (kết quả của uuid4().hex).
# Kiểm tra chặt giá trị này ở mọi endpoint nhận file_id từ client là bắt buộc:
# nó ngăn path traversal (vd "../../config") khi ghép file_id thành đường dẫn.
FILE_ID_RE = re.compile(r"[0-9a-f]{32}")


def _pdf_path(file_id: str):
    """Đường dẫn file PDF của một tài liệu."""
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str):
    """Đường dẫn file metadata của một tài liệu."""
    return UPLOAD_DIR / f"{file_id}.json"


def _build_nodes(documents, file_id: str, file_name: str):

    page_documents = []

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

    nodes = [
        node for node in nodes
        if node.text and node.text.strip()
    ]
    for index, node in enumerate(nodes):
        node.metadata["chunk_index"] = index

    return nodes


def _ingest(pdf_path, file_id: str, display_name: str) -> int:
    from app.rag.ingestion.loader import PDFLoader

    documents = PDFLoader().load_llama_parse(
        path_pdf=str(pdf_path),
        markdown_output=None,
        document_id=file_id,
    )
    if not documents:
        raise HTTPException(
            status_code=422,
            detail="LlamaParse không trích xuất được nội dung từ PDF.",
        )

    nodes = _build_nodes(documents, file_id, display_name)
    if not nodes:
        raise HTTPException(
            status_code=422,
            detail="PDF không tạo được chunk có nội dung.",
        )

    return get_index_manager().ingest_nodes(
        nodes,
        file_id=file_id,
        file_name=display_name,
    )


def _read_meta(file_id: str) -> dict:
    """Đọc metadata; trả về {} nếu thiếu hoặc hỏng để không làm hỏng cả danh sách."""
    try:
        data = json.loads(_meta_path(file_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ----------------------------------------------------------------------------
# POST /upload
# ----------------------------------------------------------------------------
@router.post("/upload")
async def upload_pdf(
    # Tên tham số `file` phải trùng key trong `fd.append('file', ...)` ở api.js,
    # nếu khác tên FastAPI sẽ trả 422 (thiếu field bắt buộc).
    file: UploadFile = File(...),
):
    # `or ""` xử lý trường hợp filename là None, tránh AttributeError ở .lower().
    name = file.filename or ""

    # Lớp kiểm tra 1: đuôi file. Chỉ là kiểm tra nhanh vì dễ bị giả mạo.
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file PDF")

    # Lớp kiểm tra 2: magic bytes. Mọi file PDF hợp lệ đều bắt đầu bằng "%PDF-",
    # không phụ thuộc vào tên file hay Content-Type do client tự khai báo.
    head = await file.read(5)
    if head != b"%PDF-":
        raise HTTPException(status_code=400, detail="File không phải PDF hợp lệ")

    # Tên hiển thị: chỉ lấy phần tên file, bỏ mọi thư mục nếu client gửi cả đường dẫn
    # (một số client gửi "C:\\thu-muc\\bao-cao.pdf"). Giá trị này chỉ dùng để hiển thị,
    # không bao giờ được dùng để ghép đường dẫn trên đĩa.
    display_name = name.replace("\\", "/").rsplit("/", 1)[-1]

    # file_id ngẫu nhiên làm định danh và tên file trên đĩa. Không dùng tên gốc vì hai
    # người có thể tải lên file trùng tên (ghi đè nhau) và tên do client kiểm soát có
    # thể chứa "../" (path traversal).
    file_id = uuid.uuid4().hex
    dest = _pdf_path(file_id)
    meta_file = _meta_path(file_id)

    # Ghi vào file tạm ".part" rồi đổi tên ở cuối. Nhờ vậy GET /files (vd người dùng
    # tải lại trang giữa lúc đang upload) không bao giờ thấy một PDF ghi dở.
    tmp = UPLOAD_DIR / f"{file_id}.part"

    size = len(head)   # đã đọc 5 byte đầu ở bước kiểm tra trên

    try:
        with tmp.open("wb") as out:
            out.write(head)   # ghi lại 5 byte đầu vì con trỏ đọc đã đi qua chúng

            # Toán tử walrus `:=` vừa đọc vừa gán; dừng khi read() trả về bytes rỗng.
            while chunk := await file.read(READ_CHUNK_BYTES):
                size += len(chunk)

                # Kiểm tra giới hạn TRONG lúc đọc để cắt ngay request quá khổ thay vì
                # để nó chiếm hết đĩa rồi mới từ chối. 413 = Payload Too Large.
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File vượt quá 50MB")

                out.write(chunk)

        # Ghi metadata trước khi "công bố" file PDF, để bất kỳ PDF nào hiện ra trong
        # danh sách đều đã có đủ thông tin.
        meta = {
            "file_id": file_id,
            "name": display_name,
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        # replace() là thao tác đổi tên nguyên tử: từ thời điểm này PDF xuất hiện đầy đủ.
        tmp.replace(dest)

    except BaseException:
        # Dọn mọi file dở dang khi có sự cố: vượt dung lượng, lỗi đĩa, hoặc client hủy
        # giữa chừng. Dùng BaseException (không phải Exception) vì khi client ngắt kết
        # nối, asyncio ném CancelledError, vốn kế thừa BaseException.
        for leftover in (tmp, dest, meta_file):
            leftover.unlink(missing_ok=True)
        raise   # ném lại đúng lỗi gốc để FastAPI trả mã HTTP phù hợp

    # Index: chỉ trả 200 khi tài liệu ĐÃ truy vấn được — khớp trạng thái 'processing' của
    # UI (bật từ xhr.upload.onload cho tới khi có response). Metadata {file_id, file_name,
    # page} gắn cho từng chunk để (a) lọc theo file_ids lúc chat, (b) hiển thị đúng tên
    # nguồn và số trang trong panel trích dẫn.
    try:
        chunks = await run_in_threadpool(_ingest, dest, file_id, display_name)
    except BaseException as exc:
        # Tài liệu không index được thì không được tồn tại: tránh PDF "ma" trong danh
        # sách Nguồn nhưng không bao giờ trả lời được. ingest_nodes đã tự rollback vector.
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
        raise   # CancelledError (client hủy giữa chừng): ném lại nguyên trạng

    # Khóa `file_id` được api.js đọc và lưu làm serverId; sau này frontend gửi lại
    # giá trị này trong `file_ids` khi chat.
    return {"file_id": file_id, "name": display_name, "size": size, "chunks": chunks}


# ----------------------------------------------------------------------------
# GET /files
# ----------------------------------------------------------------------------
@router.get("/files")
def list_files():
    """Liệt kê mọi tài liệu đã tải lên, cũ nhất trước (khớp thứ tự giao diện thêm vào).

    Khai báo bằng `def` (không phải `async def`) để FastAPI chạy hàm trong threadpool,
    vì hàm này đọc đĩa đồng bộ và không nên chặn event loop.
    """
    items = []
    for pdf in UPLOAD_DIR.glob("*.pdf"):
        file_id = pdf.stem

        # Bỏ qua file lạ do người dùng tự chép vào thư mục (tên không phải file_id).
        if not FILE_ID_RE.fullmatch(file_id):
            continue

        meta = _read_meta(file_id)
        stat = pdf.stat()
        items.append({
            "file_id": file_id,
            # Tài liệu tải lên trước khi có metadata sẽ không có tên gốc: dùng tên tạm.
            "name": meta.get("name") or f"{file_id[:8]}.pdf",
            # Dung lượng lấy từ đĩa để luôn chính xác, bất kể metadata.
            "size": stat.st_size,
            "uploaded_at": meta.get("uploaded_at")
                           or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })

    # Các chuỗi ISO-8601 cùng múi giờ UTC nên so sánh theo chuỗi cũng đúng thứ tự thời gian.
    items.sort(key=lambda item: item["uploaded_at"])
    return {"files": items}


# ----------------------------------------------------------------------------
# DELETE /files/{file_id}
# ----------------------------------------------------------------------------
@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str):
    """Xóa một tài liệu (PDF và metadata). Trả 204 No Content khi thành công."""
    # Chặn mọi giá trị không đúng dạng file_id trước khi ghép thành đường dẫn.
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(status_code=400, detail="file_id không hợp lệ")

    pdf = _pdf_path(file_id)

    # Xóa vector TRƯỚC: nếu bước này lỗi, PDF vẫn còn nên người dùng thử xóa lại được
    # (làm ngược lại sẽ để chunk mồ côi vẫn bị truy xuất mà không còn cách xóa). Cũng dọn
    # được chunk mồ côi khi PDF đã mất khỏi đĩa.
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
        # Trên Windows, file đang bị chương trình khác khóa (vd OneDrive đang đồng bộ)
        # sẽ không xóa được: báo lỗi rõ ràng thay vì để server trả 500 trống.
        raise HTTPException(status_code=500, detail=f"Không xóa được file: {e}")