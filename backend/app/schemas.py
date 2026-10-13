from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str  = Field(min_length=1, max_length=4000) # Tùy chỉnh max length
    top_k: int = Field(default=5, ge=1, le=10)
    file_ids: list[str]| None = None



    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str):
        
        if not value.strip():
            raise ValueError("Không được để trống câu hỏi")

        return value.strip()