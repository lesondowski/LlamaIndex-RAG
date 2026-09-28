from pathlib import Path
from llama_index.core import SimpleDirectoryReader
from datetime import datetime, timezone
import pymupdf

class PDFLoader:

    def load(self, file_path: str, chapter: str | None = None) -> list:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.suffix.lower() != ".pdf":
            raise ValueError("--Hệ thống chỉ tiếp nhận file pdf--")

        documents = SimpleDirectoryReader(input_files=[str(path)]).load_data() # đọc file và trả về danh sách document


        for page_number, document in enumerate(documents, start=1):

            ### setup meta data
            document.metadata.update({
                "file_name": path.name,
                "document_type": "pdf",
                "page": page_number,
                "chapter": chapter,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            })
        return documents

    def PDFtoMarkdown(self,documents, folder):
        markdown =""

        for i, doc in enumerate(documents):
            markdown += f"# Page {i + 1}\n\n"
            markdown += doc.text
            markdown += f"\n\n---\n\n"

        with open(folder, "w", encoding="utf-8") as f:
            f.write(markdown)



    def split_double_page_spread(self, input_pdf: str, output_pdf: str, ratio_threshold: float = 1.25): 


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

