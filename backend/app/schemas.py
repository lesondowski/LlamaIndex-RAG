"""Hợp đồng request cho endpoint /query/stream.

Ba nhóm trường:
  - Câu hỏi: question, top_k
  - Phạm vi lọc: file_ids, subject, grade, series, page_from, page_to
  - Lịch sử hội thoại: history (để viết lại câu hỏi nối tiếp — xem rag/generation/condense.py)

Nguyên tắc: schema chỉ chuẩn hóa cú pháp (strip, whitelist, kiểu số). Ngữ nghĩa
phạm vi — file_id có tồn tại không, page range có hợp lệ với số sách đã chọn
không — do resolve_scope xử lý, vì nó cần thêm available_file_ids.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.types import MetadataError, normalize_series, normalize_subject


class ChatTurn(BaseModel):
    """Một tin nhắn trước đó trong hội thoại. [AI]"""
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class QueryRequest(BaseModel):
    # Từ chối field lạ: gõ nhầm `subjet` phải báo lỗi ngay, không âm thầm bỏ qua
    # rồi tìm trên phạm vi rộng hơn người dùng muốn. Cũng chặn client gửi bộ lọc
    # thô (`metadata_filters`, `scope`) để chọc vào cấu trúc bên trong.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=10)

    # None = không giới hạn theo tài liệu. Danh sách rỗng bị resolve_scope từ chối
    # (báo "chưa chọn nguồn") thay vì âm thầm tìm toàn bộ kho.
    file_ids: list[str] | None = None

    # Phạm vi lọc metadata. Client chỉ gửi trường có kiểu, không gửi bộ lọc thô
    # → không thể chọc vào khóa nội bộ hay phá cấu trúc MetadataFilters.
    subject: str | None = None
    grade: int | None = Field(default=None, ge=1, le=12)
    series: str | None = None

    # Chỉ dùng được khi chọn đúng 1 file — số trang tính riêng từng sách nên
    # "trang 45" của Toán 9 không cùng nghĩa với "trang 45" của Lý 12.
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)

    # [AI] Các tin nhắn trước câu hỏi này, cũ → mới. Lịch sử lưu ở trình duyệt nên client
    # tự gửi kèm; server chỉ dùng vài tin cuối để viết lại câu hỏi nối tiếp.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Không được để trống câu hỏi")
        return value.strip()

    @field_validator("subject")
    @classmethod
    def check_subject(cls, value) -> str | None:
        # normalize_subject trả None cho chuỗi rỗng/toàn khoảng trắng — coi như
        # client không gửi trường này, không phải lỗi.
        try:
            return normalize_subject(value)
        except MetadataError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("series")
    @classmethod
    def check_series(cls, value) -> str | None:
        try:
            return normalize_series(value)
        except MetadataError as exc:
            raise ValueError(str(exc)) from None

    @model_validator(mode="after")
    def check_page_range(self):
        # Cần toàn bộ model mới so sánh được page_from với page_to.
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_from > self.page_to
        ):
            raise ValueError("page_from không được lớn hơn page_to")
        return self