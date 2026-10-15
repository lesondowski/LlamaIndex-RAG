"""Hợp đồng metadata dùng chung cho nạp tài liệu, lọc truy hồi và trích dẫn.

Nguyên tắc: mọi khóa metadata dùng để LỌC được khai báo MỘT lần ở đây. Bước nạp
(upload.py) ghi, bước truy hồi (retrieval/filters.py) đọc; không chỗ nào khác
tự gõ chuỗi khóa.

Ràng buộc kỹ thuật: giá trị metadata phải là kiểu phẳng (str hoặc int). Chroma
không nhận None hay dict, và so sánh số (GTE/LTE) chỉ đúng khi kiểu được giữ
nhất quán (page, grade, volume là int).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Khóa metadata
# ---------------------------------------------------------------------------
FILE_ID = "file_id"      # mã tài liệu (uuid4 hex, 32 ký tự)
FILE_NAME = "file_name"  # tên hiển thị
PAGE = "page"            # số trang (int, bắt đầu từ 1)
SUBJECT = "subject"      # môn học (slug trong SUBJECTS)
GRADE = "grade"          # lớp (int 1..12)
SERIES = "series"        # bộ sách (slug lowercase)
VOLUME = "volume"        # tập (int 1..4)
OWNER_ID = "owner_id"    # chỗ dành sẵn cho multi-user; do SERVER gán, không nhận từ client


# ---------------------------------------------------------------------------
# Miền giá trị hợp lệ
# ---------------------------------------------------------------------------
# Whitelist môn học: tránh "vatly", "Vật Lý", "vat_ly" thành 3 giá trị khác nhau.
SUBJECTS = frozenset({
    "toan", "vat_ly", "hoa_hoc", "sinh_hoc", "ngu_van", "lich_su", "dia_ly",
    "tin_hoc", "tieng_anh", "gdcd", "khac",
})

GRADE_MIN, GRADE_MAX = 1, 12
VOLUME_MIN, VOLUME_MAX = 1, 4

# Series chỉ validate format, không whitelist — chương trình giáo dục đổi theo năm.
SERIES_RE = re.compile(r"[a-z0-9_]{1,40}")
FILE_ID_RE = re.compile(r"[0-9a-f]{32}")


class MetadataError(ValueError):
    """Giá trị metadata hoặc phạm vi lọc không hợp lệ.

    Là lỗi của người gọi (client gửi sai), nên tầng API đổi thành HTTP 422 —
    không phải 500. Kế thừa ValueError để Pydantic field_validator wrap được.
    """


# ---------------------------------------------------------------------------
# Chuẩn hóa giá trị
# ---------------------------------------------------------------------------
def _clean(value) -> str | None:
    """Chuẩn hóa chuỗi: strip, lowercase, và coi chuỗi rỗng như không có giá trị.

    Trả None cho cả `None`, `""`, và `"   "` — giúp caller không phải phân biệt
    "không gửi" với "gửi chuỗi rỗng", vì cả hai đều mang nghĩa "không lọc".
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def normalize_subject(value) -> str | None:
    """Chuẩn hóa môn học: chỉ nhận slug trong SUBJECTS, ngược lại raise MetadataError."""
    text = _clean(value)
    if text is None:
        return None
    if text not in SUBJECTS:
        raise MetadataError(
            f"Môn học '{text}' không hợp lệ. Chỉ nhận: {', '.join(sorted(SUBJECTS))}."
        )
    return text


def normalize_series(value) -> str | None:
    """Chuẩn hóa bộ sách: chỉ nhận [a-z0-9_] tối đa 40 ký tự."""
    text = _clean(value)
    if text is None:
        return None
    if not SERIES_RE.fullmatch(text):
        raise MetadataError(
            "Bộ sách chỉ gồm chữ thường không dấu, số và dấu gạch dưới, tối đa 40 ký tự."
        )
    return text


def _int_in_range(value, name: str, low: int, high: int) -> int | None:
    """Chuẩn hóa số nguyên trong khoảng [low, high].

    Chấp nhận cả str và int (form data thường là str). Từ chối bool vì trong
    Python `isinstance(True, int)` là True — nếu không chặn riêng, `grade=True`
    sẽ thành `grade=1` và qua mặt validate.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise MetadataError(f"{name} phải là số nguyên.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise MetadataError(f"{name} phải là số nguyên.") from None
    if not low <= number <= high:
        raise MetadataError(f"{name} phải nằm trong khoảng {low} đến {high}.")
    return number


def normalize_grade(value) -> int | None:
    return _int_in_range(value, "Lớp", GRADE_MIN, GRADE_MAX)


def normalize_volume(value) -> int | None:
    return _int_in_range(value, "Tập", VOLUME_MIN, VOLUME_MAX)


# ---------------------------------------------------------------------------
# Nhãn tài liệu (ghi lúc upload)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocumentTags:
    """Nhãn mô tả một cuốn sách, người dùng khai báo lúc tải lên.

    Tất cả trường đều tùy chọn — sách không có nhãn vẫn upload được, chỉ không
    lọc được theo môn/lớp/bộ sách. Frozen để dùng làm dict key nếu cần.
    """

    subject: str | None = None
    grade: int | None = None
    series: str | None = None
    volume: int | None = None

    @classmethod
    def from_input(
        cls,
        subject=None,
        grade=None,
        series=None,
        volume=None,
    ) -> "DocumentTags":
        """Chuẩn hóa đầu vào thô (str từ form) → DocumentTags đã validate.

        Raise MetadataError nếu bất kỳ trường nào sai — caller (route /upload)
        bắt và đổi thành HTTP 422.
        """
        return cls(
            subject=normalize_subject(subject),
            grade=normalize_grade(grade),
            series=normalize_series(series),
            volume=normalize_volume(volume),
        )

    def to_metadata(self) -> dict:
        """Dict chỉ gồm khóa có giá trị — không bao giờ đưa None vào metadata Chroma.

        Chroma từ chối None trong metadata. Nếu để `{subject: None, grade: 9}`,
        Chroma sẽ raise khi insert. Hàm này lọc bỏ None trước khi trả về.
        """
        pairs = (
            (SUBJECT, self.subject),
            (GRADE, self.grade),
            (SERIES, self.series),
            (VOLUME, self.volume),
        )
        return {key: value for key, value in pairs if value is not None}


# ---------------------------------------------------------------------------
# Phạm vi truy vấn (dùng lúc chat)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QueryScope:
    """Phạm vi tìm kiếm của MỘT câu hỏi. Các điều kiện kết hợp bằng AND.

    file_ids:
        None  → không giới hạn theo tài liệu (tìm toàn bộ collection).
        tuple → chỉ tìm trong các tài liệu này.
        ()    → KHÔNG hợp lệ ("chưa chọn nguồn"); resolve_scope từ chối.

    owner_id:
        Do server gán từ phiên đăng nhập, không bao giờ lấy từ request của
        client. Chỗ dành sẵn cho multi-user, hiện tại luôn None.
    """

    file_ids: tuple[str, ...] | None = None
    subject: str | None = None
    grade: int | None = None
    series: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    owner_id: str | None = None

    def as_dict(self) -> dict:
        """Các điều kiện đang bật — dùng để ghi log mà không lộ trường None."""
        return {key: value for key, value in self.__dict__.items() if value is not None}