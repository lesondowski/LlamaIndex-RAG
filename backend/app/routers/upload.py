# ============================================================================
# upload.py — Router quản lý tài liệu: tải lên, liệt kê, xóa
#
# Mỗi tài liệu được lưu thành hai file cạnh nhau trong UPLOAD_DIR:
#   <file_id>.pdf    nội dung PDF gốc
#   <file_id>.json   metadata hiển thị (tên gốc, dung lượng, thời điểm tải lên)
# Lý do cần file metadata: tên file trên đĩa là file_id ngẫu nhiên, nên nếu không
# lưu tên gốc thì sau khi tải lại trang sẽ không thể dựng lại danh sách "Nguồn".
#
# Endpoint:
#   POST   /upload           nhận PDF, trả { file_id, name, size }
#   GET    /files            liệt kê tài liệu đã tải lên
#   DELETE /files/{file_id}  xóa tài liệu
# ============================================================================
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import MAX_FILE_SIZE, UPLOAD_DIR

# APIRouter gom các endpoint liên quan; main.py gắn router này vào app chính.
router = APIRouter()

# Đọc theo từng khối 1 MB: RAM luôn ở mức ~1 MB thay vì nạp cả file vào bộ nhớ.
CHUNK_SIZE = 1024 * 1024

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
            while chunk := await file.read(CHUNK_SIZE):
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

    # TODO (bước tiếp theo của luồng RAG): chunking -> embedding -> lưu vector store.
    # Khi index, gắn metadata {file_id, file_name, page} cho từng chunk để:
    #   (a) lọc truy vấn theo tài liệu người dùng chọn (file_ids ở request chat),
    #   (b) hiển thị đúng tên nguồn và số trang trong panel trích dẫn.
    # ingest_pdf(dest, file_id=file_id, file_name=display_name)

    # Khóa `file_id` được api.js đọc và lưu làm serverId; sau này frontend gửi lại
    # giá trị này trong `file_ids` khi chat.
    return {"file_id": file_id, "name": display_name, "size": size}


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
    if not pdf.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

    try:
        pdf.unlink()
        _meta_path(file_id).unlink(missing_ok=True)
    except OSError as e:
        # Trên Windows, file đang bị chương trình khác khóa (vd OneDrive đang đồng bộ)
        # sẽ không xóa được: báo lỗi rõ ràng thay vì để server trả 500 trống.
        raise HTTPException(status_code=500, detail=f"Không xóa được file: {e}")

    # TODO: khi đã có bước index, xóa luôn các chunk của file_id này khỏi vector store
    # (cùng markdown/nodes trung gian), nếu không tài liệu đã xóa vẫn được truy xuất.