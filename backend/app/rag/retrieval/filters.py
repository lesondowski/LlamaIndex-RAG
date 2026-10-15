"""Dựng bộ lọc metadata cho truy hồi: QueryScope → MetadataFilters của LlamaIndex.

Đây là lớp DUY NHẤT quyết định "được phép tìm trong đâu":
  - resolve_scope() — xác thực + chuẩn hóa đầu vào thô của client.
  - build_filters() — đổi QueryScope thành bộ lọc Chroma (tiền lọc: Chroma lọc
    metadata trước, rồi mới tìm vector gần nhất).

Client chỉ gửi trường có kiểu; không gửi bộ lọc thô → không thể lọc theo khóa
nội bộ hay phá cấu trúc MetadataFilters.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable

from llama_index.core.vector_stores import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from app.rag.types import (
    FILE_ID,
    FILE_ID_RE,
    GRADE,
    OWNER_ID,
    PAGE,
    SERIES,
    SUBJECT,
    MetadataError,
    QueryScope,
    normalize_grade,
    normalize_series,
    normalize_subject,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _positive_int(value, name: str) -> int | None:
    """Số nguyên >= 1, hoặc None nếu không có giá trị.

    Chặn bool trước int: isinstance(True, int) là True, nếu không chặn riêng
    thì True sẽ thành 1 và qua mặt validate.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MetadataError(f"{name} phải là số nguyên từ 1 trở lên.")
    return value


# ---------------------------------------------------------------------------
# Xác thực đầu vào (đường vào duy nhất từ client)
# ---------------------------------------------------------------------------
def resolve_scope(
    *,
    file_ids: Iterable[str] | None = None,
    subject=None,
    grade=None,
    series=None,
    page_from: int | None = None,
    page_to: int | None = None,
    available_file_ids: Collection[str] | None = None,
    owner_id: str | None = None,
) -> QueryScope:
    """Xác thực và chuẩn hóa đầu vào thô của client thành QueryScope.

    Ngữ nghĩa file_ids:
      - None  → tìm trong mọi tài liệu (vẫn chịu các điều kiện còn lại).
      - rỗng  → lỗi "chưa chọn nguồn" (tránh vô tình tìm toàn bộ kho).
      - có id → dedupe, bỏ id không còn tồn tại; hết id thì lỗi.

    page_from/page_to chỉ dùng được với đúng một tài liệu — số trang tính riêng
    từng sách nên "trang 45" của Toán 9 khác "trang 45" của Lý 12.

    Raise MetadataError cho mọi đầu vào không hợp lệ. Tầng API bắt và đổi
    thành HTTP 422.
    """
    resolved_ids: tuple[str, ...] | None = None
    if file_ids is not None:
        unique = list(dict.fromkeys(file_ids))   # dedupe, giữ thứ tự
        if not unique:
            raise MetadataError("Chưa chọn nguồn nào để tìm.")
        for file_id in unique:
            if not isinstance(file_id, str) or not FILE_ID_RE.fullmatch(file_id):
                raise MetadataError("file_ids chứa mã tài liệu không hợp lệ.")
        if available_file_ids is not None:
            unique = [fid for fid in unique if fid in available_file_ids]
            if not unique:
                raise MetadataError("Các nguồn đã chọn không còn tồn tại.")
        resolved_ids = tuple(unique)

    page_from = _positive_int(page_from, "page_from")
    page_to = _positive_int(page_to, "page_to")
    if page_from is not None and page_to is not None and page_from > page_to:
        raise MetadataError("page_from không được lớn hơn page_to.")
    if (page_from is not None or page_to is not None) and (
        resolved_ids is None or len(resolved_ids) != 1
    ):
        raise MetadataError("Lọc theo trang chỉ dùng được khi chọn đúng một sách.")

    return QueryScope(
        file_ids=resolved_ids,
        subject=normalize_subject(subject),
        grade=normalize_grade(grade),
        series=normalize_series(series),
        page_from=page_from,
        page_to=page_to,
        owner_id=owner_id,
    )


# ---------------------------------------------------------------------------
# Dựng bộ lọc cho LlamaIndex
# ---------------------------------------------------------------------------
def build_filters(scope: QueryScope | None) -> MetadataFilters | None:
    """Đổi QueryScope thành MetadataFilters, kết hợp mọi điều kiện bằng AND.

    Trả None khi không có điều kiện nào — nghĩa là không lọc, tìm toàn collection.

    Raise MetadataError nếu scope.file_ids là tuple rỗng: đây là trạng thái
    không hợp lệ (chưa chọn nguồn) — resolve_scope đã chặn, nhưng giữ guard ở
    đây để bảo vệ caller gọi build_filters trực tiếp.
    """
    if scope is None:
        return None

    conditions: list[MetadataFilter] = []

    if scope.file_ids is not None:
        if not scope.file_ids:
            raise MetadataError("Chưa chọn nguồn nào để tìm.")
        if len(scope.file_ids) == 1:
            conditions.append(MetadataFilter(
                key=FILE_ID, value=scope.file_ids[0], operator=FilterOperator.EQ,
            ))
        else:
            conditions.append(MetadataFilter(
                key=FILE_ID, value=list(scope.file_ids), operator=FilterOperator.IN,
            ))

    if scope.subject is not None:
        conditions.append(MetadataFilter(
            key=SUBJECT, value=scope.subject, operator=FilterOperator.EQ,
        ))
    if scope.grade is not None:
        conditions.append(MetadataFilter(
            key=GRADE, value=scope.grade, operator=FilterOperator.EQ,
        ))
    if scope.series is not None:
        conditions.append(MetadataFilter(
            key=SERIES, value=scope.series, operator=FilterOperator.EQ,
        ))
    if scope.page_from is not None:
        conditions.append(MetadataFilter(
            key=PAGE, value=scope.page_from, operator=FilterOperator.GTE,
        ))
    if scope.page_to is not None:
        conditions.append(MetadataFilter(
            key=PAGE, value=scope.page_to, operator=FilterOperator.LTE,
        ))
    if scope.owner_id is not None:
        conditions.append(MetadataFilter(
            key=OWNER_ID, value=scope.owner_id, operator=FilterOperator.EQ,
        ))

    if not conditions:
        return None
    return MetadataFilters(filters=conditions, condition=FilterCondition.AND)