from pathlib import Path
import json
from collections.abc import Sequence
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser,SentenceSplitter
CHUNK_SIZE = 700
CHUNK_OVERLAP = 64
class Chunking:
    
    def __init__( self,
        output_file: str | Path | None = None,
        chunk_size: int = 550, # chunk_size này không phải là đơn vị ký tự, lưu ý đây là token chứ không phai ký tự
        chunk_overlap: int = 64, # Mức độ chồng nhau của các câu, set vừa đủ để k bị mất ngữ cảnh
    ) -> None:
        
        self.output_file = Path(output_file) if output_file is not None else None
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    def validate_documents(self, documents: Sequence[Document]):
        valid_docs = [
            document for document in documents
            if document.text and document.text.strip()
        ]
        if not valid_docs:
            return []
        required_keys = ["document_id", "source", "file_name"]
        for doc in valid_docs:
            metadata = doc.metadata or {}
            for key in required_keys:
                if not metadata.get(key):
                    raise ValueError(f"Document không có metadata - không có key bắt buộc{key}")
            if metadata.get("page") is None:
                raise ValueError("Document thiếu metadaata - Thiếu page")

        return valid_docs

        return doc
    def chunk(self, documents: Sequence[Document]):
        page_documents = self.validate_documents(documents)        
        if not page_documents:
            return []
        #Như cũ      
        sections = MarkdownNodeParser().get_nodes_from_documents(page_documents) # chia theo heading

        splitter = SentenceSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        chunks = []

        for section_index, section in enumerate(sections):
            section_meta = dict(section.metadata) if section.metadata else {}
            section_document = Document(text=section.text, metadata=section_meta)

            section_chunks = splitter.get_nodes_from_documents([section_document])

            for chunk_index, node in enumerate(section_chunks):
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


    def save(self, nodes: list) -> None:
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
        nodes = self.chunk(documents)
        self.save(nodes)

        return nodes
