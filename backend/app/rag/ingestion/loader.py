from pathlib import Path
from llama_index.core import SimpleDirectoryReader, Document
from unstructured.partition.pdf import partition_pdf
from datetime import datetime, timezone
import pymupdf
import re
import pymupdf4llm



class PDFLoader:
    def load(self, file_path: str, document_id: str | None = None) -> list[Document]:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.suffix.lower() != ".pdf":
            raise ValueError("Hệ thống chỉ tiếp nhận file PDF.")

        page_chunks = pymupdf4llm.to_markdown(
            str(path),
            page_chunks=True,
            show_progress=False,
        )
        resolved_document_id = document_id or path.stem # Thêm document ID vào metadata
        documents: list[Document] = []

        for index, page_chunk in enumerate(page_chunks, start=1):
            page_metadata = page_chunk.get("metadata", {})
            page_number = page_metadata.get("page", index)
            markdown = (page_chunk.get("text") or "").strip()

            if not markdown:
                continue

            documents.append(
                Document(
                    text=markdown,
                    metadata={
                        "document_id": resolved_document_id,
                        "file_name": path.name,
                        "source": str(path.resolve()),
                        "document_type": "pdf",
                        "parser": "pymupdf4llm",
                        "page": page_number,
                        "page_count": len(page_chunks),
                    },
                )
            )

        return documents

    def PDFtoMarkdown(
        self,
        documents: list[Document],
        output_file: str,
    ) -> None:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        markdown_pages: list[str] = []

        for document in documents:
            page = document.metadata.get("page", "")
            markdown_pages.append(
                f"<!-- Page {page} -->\n\n{document.text.strip()}"
            )

        output_path.write_text(
            "\n\n".join(markdown_pages),
            encoding="utf-8",
        )

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
