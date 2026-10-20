import os
import pymupdf
import pymupdf4llm
from pathlib import Path
from dotenv import load_dotenv
from typing import Any
from collections import defaultdict
from llama_index.core import Document
from unstructured.partition.pdf import partition_pdf
from llama_index.readers.llama_parse import LlamaParse

load_dotenv() # nhớ setup api key

class PDFLoader:
    def load_partition_pdf(
        self,
        file_path: str,
        document_id: str | None = None,
    ) -> list[Document]:
        path = self._validate_pdf_path(file_path)
        resolved_document_id = document_id or path.stem

        elements = partition_pdf(
            filename=str(path),
            strategy="ocr_only",
            languages=["vie"],
        )

        pages: dict[int, list[str]] = defaultdict(list)
        page_element_types: dict[int, list[str]] = defaultdict(list)

        for element in elements:
            text = str(element).strip()

            if not text:
                continue

            page_number = self._get_page_number(element)
            element_type = self._get_element_type(element)

            pages[page_number].append(
                f"[{element_type}]\n{text}"
            )
            page_element_types[page_number].append(element_type)

        documents: list[Document] = []

        for page_number in sorted(pages):
            page_text = "\n\n".join(pages[page_number]).strip()

            if not page_text:
                continue

            documents.append(
                Document(
                    text=page_text,
                    metadata={
                        "document_id": resolved_document_id,
                        "doc_id": resolved_document_id,
                        "file_name": path.name,
                        "source": str(path.resolve()),
                        "document_type": "pdf",
                        "source_type": "pdf",
                        "parser": "unstructured.partition_pdf",
                        "scan": True,
                        "ocr": True,
                        "page": page_number,
                        "element_types": sorted(
                            set(page_element_types[page_number])
                        ),
                    },
                )
            )

        return documents

    def _validate_pdf_path(self, file_path: str) -> Path:
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.suffix.lower() != ".pdf":
            raise ValueError("Hệ thống chỉ tiếp nhận file PDF.")

        return path

    def _get_page_number(self, element: Any) -> int:
        metadata = getattr(element, "metadata", None)

        if metadata is None:
            return 1

        page_number = getattr(metadata, "page_number", 1)

        try:
            return int(page_number)
        except (TypeError, ValueError):
            return 1

    def _get_element_type(self, element: Any) -> str:
        category = getattr(element, "category", None)

        if category:
            return str(category)

        return element.__class__.__name__

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

    def load_llama_parse(self,path_pdf: str, markdown_output: str | Path | None = None ,document_id: str | None = None,) -> list[Document]:
        """
        Parse PDF bằng LlamaParse.

        Args:
            path_pdf: Đường dẫn tới file PDF.
            markdown_output: Đường dẫn file Markdown đầu ra.
            document_id: ID của document.

        Returns:
            Danh sách Document của LlamaIndex.
        """
        api_key = os.getenv("LLAMA_CLOUD_API_KEY")

        if not api_key:
            raise ValueError( "Chưa setup API KEY LLAMAINDEX.")

        path = self._validate_pdf_path(path_pdf)
        resolved_document_id = (document_id or path.stem)
        print(f"PDF: {path}")
        print(f"Document ID: {resolved_document_id}")
        try:
            reader = LlamaParse(api_key=api_key,result_type="markdown",verbose=True,ignore_errors=False)

            parsed_documents = reader.load_data(str(path))
        except Exception as exc:
            print("=" * 70)
            print("LLAMAPARSE ERROR")
            print("=" * 70)
            print(f"Type: {type(exc).__name__}")
            print(f"Message: {exc}")
            raise
        print(f"LlamaParse returned {len(parsed_documents)} document(s).")
        if not parsed_documents:
            raise RuntimeError("LlamaParse không trả về Document nào.")

        if markdown_output is not None:
            output_path = Path(markdown_output)
            output_path.parent.mkdir(parents=True,exist_ok=True,)

            markdown_parts = []

            for doc in parsed_documents:
                if doc.text and doc.text.strip():
                    markdown_parts.append(doc.text.strip())

            if not markdown_parts:
                raise RuntimeError(
                    "LlamaParse có trả về Document "
                    "nhưng tất cả Document đều không có nội dung."
                )

            markdown_text = "\n\n---\n\n".join(markdown_parts)
            output_path.write_text(markdown_text,encoding="utf-8",)

        final_documents: list[Document] = []

        for index, doc in enumerate(parsed_documents,start=1,):
            if not doc.text:
                continue
            metadata = dict(getattr(doc,"metadata",{},) or {})

            # Ưu tiên page metadata từ LlamaParse
            page = ( metadata.get("page") or metadata.get("page_number") or index)

            final_documents.append(
                Document(
                    text=doc.text,
                    metadata={
                        "document_id": (resolved_document_id),
                        "doc_id": (resolved_document_id),
                        "file_name": path.name,
                        "source": str(path.resolve()),
                        "document_type": "pdf",
                        "source_type": "pdf",
                        "parser": "llamacloud",
                        "parser_strategy": "markdown",
                        "page": page,
                    },
                )
            )
        if not final_documents:
            raise RuntimeError("LlamaParse không tạo được Document có nội dung.")
        return final_documents