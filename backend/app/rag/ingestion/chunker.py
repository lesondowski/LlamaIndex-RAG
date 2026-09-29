from dataclasses import dataclass
from enum import StrEnum
from typing import Any, List
import pymupdf
from llama_index.core.schema import TextNode



class ElementType(StrEnum):
    PARAGRAPH = "paragraph" # loại đoạn văn
    TABLE = "table" # dạng bảng


@dataclass
class PDFElement:

    id: str
    page_n: int
    type: ElementType
    text: str
    metadata: dict[str, Any]

class BasicPDFExtractor:

    def extract(self, pdf_path: str) -> list[PDFElement]:


        doc = pymupdf.open(pdf_path)
        element: list[PDFElement] = []
        
        
        for page_index, page in enumerate(doc):

            page_number = page_index + 1
            
            element.extend(self._extract_tabels(page, page_number))



