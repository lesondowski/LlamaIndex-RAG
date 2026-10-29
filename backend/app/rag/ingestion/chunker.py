"""Chia Document (mỗi Document = 1 trang PDF đã parse sang Markdown) thành Node/chunk.

Chiến lược chia 2 tầng:
  1. MarkdownNodeParser — cắt theo heading (#, ##, ...) để mỗi section là một
     mục trọn vẹn của sách, tránh một chunk trộn nội dung của hai mục khác nhau.
  2. SentenceSplitter — section nào dài hơn chunk_size thì cắt tiếp theo câu,
     có overlap để không mất ngữ cảnh ở ranh giới hai chunk.

Vì đầu vào đã tách theo trang, mỗi chunk luôn thuộc đúng MỘT trang → metadata
"page" chính xác cho citation.

Được dùng ở:
  - app/routers/upload.py — ingest khi người dùng upload (không ghi file JSON)
  - app/rag/pipeline.py   — chạy pipeline từ dòng lệnh, có ghi nodes ra JSON
"""
from pathlib import Path
import json
from collections.abc import Sequence
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser,SentenceSplitter
CHUNK_SIZE = 700
CHUNK_OVERLAP = 126 #18%



class Chunking:
    """Chia danh sách Document theo trang thành các Node sẵn sàng để embedding.

    Args:
        output_file: Đường dẫn file JSON để save() ghi nodes ra. Để None khi chỉ
            cần chunk trong bộ nhớ (VD: route upload).
        chunk_size: Số token tối đa của một chunk.
        chunk_overlap: Số token lặp lại giữa hai chunk liền kề.
    """

    def __init__( self,
        output_file: str | Path | None = None,
        chunk_size: int = CHUNK_SIZE, # chunk_size này không phải là đơn vị ký tự, lưu ý đây là token chứ không phai ký tự
        chunk_overlap: int = CHUNK_OVERLAP, # Mức độ chồng nhau của các câu, set vừa đủ để k bị mất ngữ cảnh
    ) -> None:
        
        self.output_file = Path(output_file) if output_file is not None else None
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------------
    # Validate đầu vào
    # ------------------------------------------------------------------------
    def validate_documents(self, documents: Sequence[Document]):
        """Bỏ Document rỗng và kiểm tra metadata bắt buộc của các Document còn lại.

        Fail-fast: thiếu metadata nguồn thì báo lỗi ngay ở bước chunk, thay vì để
        chunk không trích dẫn được lọt vào vector store.

        Raises:
            ValueError: Document thiếu document_id / source / file_name / page.
        """
        # Chỉ giữ Document có nội dung thật (không rỗng, không toàn khoảng trắng).
        valid_docs = [
            document for document in documents
            if document.text and document.text.strip()
        ]
        if not valid_docs:
            return []

        # Metadata tối thiểu để truy vết nguồn cho citation.
        required_keys = ["document_id", "source", "file_name"]
        for doc in valid_docs:
            metadata = doc.metadata or {}
            for key in required_keys:
                if not metadata.get(key):
                    raise ValueError(f"Document không có metadata - không có key bắt buộc{key}")
            # Kiểm tra `is None` thay vì falsy để không chặn nhầm giá trị hợp lệ như 0.
            if metadata.get("page") is None:
                raise ValueError("Document thiếu metadaata - Thiếu page")

        return valid_docs

    # ------------------------------------------------------------------------
    # Chia chunk
    # ------------------------------------------------------------------------
    def chunk(self, documents: Sequence[Document]):
        """Chia Document → section (theo heading) → chunk (theo câu).

        Metadata gốc của Document (file, trang, tag môn/lớp...) được giữ trên mọi
        chunk, kèm thêm:
          - section_index:     thứ tự section trong lần chunk này
          - chunk_index:       thứ tự chunk bên trong section đó
          - content_type:      "MD" (nội dung Markdown)
          - text_lenght_chars: độ dài nội dung tính theo ký tự

        Returns:
            Danh sách Node theo thứ tự xuất hiện trong tài liệu; rỗng nếu không có
            Document nào có nội dung.
        """
        page_documents = self.validate_documents(documents)
        if not page_documents:
            return []
        #Như cũ
        # Tầng 1: cắt theo heading Markdown. Mỗi section kế thừa metadata của
        # trang chứa nó (và có thêm "header_path" do MarkdownNodeParser gắn).
        sections = MarkdownNodeParser().get_nodes_from_documents(page_documents) # chia theo heading

        # Tầng 2: section dài hơn chunk_size được cắt tiếp theo câu.
        splitter = SentenceSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        chunks = []

        for section_index, section in enumerate(sections):
            # Bọc section thành Document mới để SentenceSplitter giữ metadata của nó.
            section_meta = dict(section.metadata) if section.metadata else {}
            section_document = Document(text=section.text, metadata=section_meta)

            section_chunks = splitter.get_nodes_from_documents([section_document])

            for chunk_index, node in enumerate(section_chunks):
                # Gắn vị trí của chunk và ghi lại tường minh các khóa nguồn để
                # citation luôn đọc được, kể cả khi parser trung gian làm rơi metadata.
                final_meta = dict(node.metadata) if node.metadata else {}
                final_meta.update(
                    {
                        "section_index": section_index,
                        "chunk_index": chunk_index,
                        "content_type": "MD",
                        "text_lenght_chars": len(node.text),
                        "source": final_meta.get("source", ""),
                        "file_name": final_meta.get("file_name", ""),
                        "document_id": final_meta.get("document_id", ""),
                        "page": final_meta.get("page", None),
                    }
                )
                node.metadata = final_meta
                chunks.append(node)


        return chunks

    # ------------------------------------------------------------------------
    # Xuất kết quả
    # ------------------------------------------------------------------------
    def save(self, nodes: list) -> None:
        """Ghi nodes ra file JSON (id, text, metadata) để kiểm tra / làm minh chứng.

        Raises:
            ValueError: Chunking được tạo mà không có output_file.
        """
        if self.output_file is None:
            raise ValueError("Cần truyền output_file để lưu chunks.")
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        data = [
            {
                "id": node.node_id,
                "text": node.text,
                "metadata": node.metadata
            }
            for node in nodes
        ]
        self.output_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")



    def run(self, documents: Sequence[Document]) -> list:
        """Chunk rồi lưu ra output_file trong một bước; trả về danh sách nodes."""
        nodes = self.chunk(documents)
        self.save(nodes)

        return nodes
