import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import MAX_FILE_SIZE, UPLOAD_DIR

log = logging.getLogger(__name__)
router = APIRouter()

READ_CHUNK_BYTES = 1024 * 1024
FILE_ID_RE = re.compile(r"[0-9a-f]{32}")


def _pdf_path(file_id: str):
    return UPLOAD_DIR / f"{file_id}.pdf"


def _meta_path(file_id: str):
    return UPLOAD_DIR / f"{file_id}.json"


def _write_meta(file_id: str, metadata: dict) -> None:
    target = _meta_path(file_id)
    temporary = target.with_suffix(".json.tmp")

    temporary.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def _read_meta(file_id: str) -> dict:
    try:
        metadata = json.loads(_meta_path(file_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return metadata if isinstance(metadata, dict) else {}


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    original_name = file.filename or ""
    display_name = original_name.replace("\\", "/").rsplit("/", 1)[-1]

    if not display_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file PDF.")

    file_header = await file.read(5)
    if file_header != b"%PDF-":
        raise HTTPException(status_code=400, detail="File không có chữ ký PDF hợp lệ.")

    file_id = uuid.uuid4().hex
    destination = _pdf_path(file_id)
    temporary_pdf = UPLOAD_DIR / f"{file_id}.part"
    metadata_path = _meta_path(file_id)

    size = len(file_header)

    try:
        if size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {MAX_FILE_SIZE // (1024 * 1024)} MB.",
            )

        with temporary_pdf.open("xb") as output:
            output.write(file_header)

            while data := await file.read(READ_CHUNK_BYTES):
                size += len(data)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File vượt quá giới hạn {MAX_FILE_SIZE // (1024 * 1024)} MB.",
                    )
                output.write(data)

        metadata = {
            "file_id": file_id,
            "name": display_name,
            "size": size,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "status": "saved",
        }
        _write_meta(file_id, metadata)

        # Chỉ chuyển sang tên .pdf sau khi ghi xong toàn bộ nội dung.
        temporary_pdf.replace(destination)

    except HTTPException:
        temporary_pdf.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary_pdf.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        log.exception("Không lưu được PDF upload.")
        raise HTTPException(
            status_code=500,
            detail="Không thể lưu file vào thư mục local của project.",
        ) from exc
    except BaseException:
        temporary_pdf.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return {
        "file_id": file_id,
        "name": display_name,
        "size": size,
        "status": "saved",
    }


@router.get("/files")
def list_files():
    files = []

    for pdf_path in UPLOAD_DIR.glob("*.pdf"):
        file_id = pdf_path.stem
        if not FILE_ID_RE.fullmatch(file_id):
            continue

        metadata = _read_meta(file_id)
        stat = pdf_path.stat()

        files.append(
            {
                "file_id": file_id,
                "name": metadata.get("name") or pdf_path.name,
                "size": stat.st_size,
                "uploaded_at": metadata.get("uploaded_at")
                or datetime.fromtimestamp(
                    stat.st_mtime,
                    timezone.utc,
                ).isoformat(),
                # Luồng này chỉ lưu file, chưa parse/index.
                "status": "saved",
            }
        )

    files.sort(key=lambda item: item["uploaded_at"])
    return {"files": files}


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: str):
    if not FILE_ID_RE.fullmatch(file_id):
        raise HTTPException(status_code=400, detail="file_id không hợp lệ.")

    pdf_path = _pdf_path(file_id)
    metadata_path = _meta_path(file_id)

    if not pdf_path.is_file() and not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy file.")

    try:
        pdf_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
    except OSError as exc:
        log.exception("Không xóa được file local: %s", file_id)
        raise HTTPException(
            status_code=500,
            detail="Không thể xóa file khỏi thư mục local.",
        ) from exc