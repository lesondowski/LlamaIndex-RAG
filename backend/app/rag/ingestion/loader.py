from pathlib import Path
from llama_index.core import SimpleDirectoryReader, Document
from unstructured.partition.pdf import partition_pdf
from datetime import datetime, timezone
import pymupdf
import re

class PDFLoader:

    def load(self, file_path: str, chapter: str | None = None) -> list:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.suffix.lower() != ".pdf":
            raise ValueError("--Hệ thống chỉ tiếp nhận file pdf--")

        elements = partition_pdf(
            filename=str(path),
            strategy="fast", # xử lý nhiều cột, nếu cài Tesseract OCR thì dùng auto hoặc hi_res
            infer_table_structure=True
        )

        documents = []


        for idx, element in enumerate(elements):
            
            
            category = type(element).__name__
            if category in ["Header", "Footer"]: continue
            if category not in ["Title", "NarrativeText", "Text", "ListItem", "Table"]: continue

            
            
            text = (element.text or "").strip()

            if not text: continue

            metadata = {
                "file_name": path.name,
                "document_type": "pdf",
                "element_type": category,
                "element_id": idx,
                "page": getattr(element.metadata, "page_number", None),
                "ingested_at":  datetime.now(timezone.utc).isoformat()
            }

            if category == "Table":
                text_as_html = getattr(element.metadata, "text_as_html", None)
                if text_as_html:
                    metadata["text_as_html"] = text_as_html

            documents.append(Document(text=text, metadata=metadata))

        return documents

    def PDFtoMarkdown(self,documents:list[Document], output_file:str):
        markdown =[]
        current_page=None

        for doc in documents:
                page = doc.metadata.get("page")
                category = doc.metadata.get("element_type")

                if page != current_page:

                    markdown.append(f"\n\n# Page {page}\n")
                    current_page=page

                if category=="Title":
                    markdown.append(f"\n## {doc.text}\n")
                elif category=="ListItem":
                    markdown.append(f"- {doc.text}")
                elif category=="Table":
                    markdown.append(f"""```table{doc.text}""")
                else:
                    markdown.append(doc.text)

        content="\n\n".join(markdown)
        with open(output_file,"w",encoding="utf-8") as f:
            f.write(content)



    def split_double_page_spread(self, input_pdf: str, output_pdf: str, ratio_threshold: float = 1.25): 
        """
        Cắt pdf có double page
        """

        src = pymupdf.open(input_pdf)
        dst = pymupdf.open()   

        page_map = []

        for page_idx, page in enumerate(src):
            rect = page.rect
            w, h = rect.width, rect.height # Lấy kích thước độ dài pdf
            is_spread = w > h * ratio_threshold # check double-page
            print(
            f"Đang xử lý PDF page {page_idx + 1}: "
            f"{w:.1f} x {h:.1f}"
        )

            if is_spread:
                mid_x = rect.x0 + w/2 # lấy tọa độ giữa trang
                # cắt trang
                crops = [
                    ("left", pymupdf.Rect(rect.x0, rect.y0, mid_x, rect.y1)),
                    ("right", pymupdf.Rect(mid_x, rect.y0, rect.x1, rect.y1))
                ]
                for side, clip in crops:
                    new_page = dst.new_page(width=clip.width, height=clip.height)
                    new_page.show_pdf_page(new_page.rect, src, page_idx, clip=clip)

                    page_map.append({
                        "source_pdf_page": page_idx + 1,
                        "side": side,
                        "logical_page": len(page_map) + 1,

                    }) # tạo trang mới + setup kích thước

            else:
                dst.insert_pdf(src, from_page=page_idx, to_page=page_idx)
                page_map.append({
                    "source_pdf_page": page_idx + 1,
                    "side": "full",
                    "logical_page": len(page_map) + 1,
                })
        dst.save(output_pdf)
        dst.close()
        src.close()
        return page_map


