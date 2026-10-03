from pathlib import Path
import json
from collections.abc import Sequence
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser,SentenceSplitter

class Chunking:
    
    def __init__( self,
        output_file: str | Path,
        chunk_size: int = 550, # chunk_size này không phải là đơn vị ký tự, lưu ý đây là token chứ không phai ký tự
        chunk_overlap: int = 64, # Mức độ chồng nhau của các câu, set vừa đủ để k bị mất ngữ cảnh
    ) -> None:
        
        self.output_file = Path(output_file)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap


    def create_document(self, markdown: str) -> Document:

        doc = Document(
            text = markdown,
            metadata = {
                "source": str(self.markdown_file.resolve()),
                "file_name": self.markdown_file.name,
                "document_type": "PDF",
            }
        )

        return doc
    def chunk(self, doucuments: Sequence[Document]):
        
        # kiểm tra và loại bỏ các text rỗng trong document
        page_documents = [
            document 
            for document in doucuments 
            if document.text and document.text.strip()
            ]

        if not page_documents:
            return []
        key_metadata = ["document_id", "source", "file_name"]
        for document in page_documents:
            metadata = document.metadata
            for key in key_metadata:
                if not metadata.get(key):
                    raise ValueError(f"Document thiếu metadata - Key: {key}")
            if metadata.get("page") is None:
                    raise ValueError(f"Document thiếu metadata - page")


        #Như cũ      
        sections = MarkdownNodeParser().get_nodes_from_documents(page_documents) # chia theo heading

        splitter = SentenceSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        chunks = []

        for section_index, section in enumerate(sections):
            section_document = Document(text=section.text, metadata=dict(section.metadata))

            section_chunks = splitter.get_nodes_from_documents([section_document])

            for chunk_index, node in enumerate(section_chunks):
                node.metadata.update(
                    {
                        "section_index": section_index,
                        "chunk_index": chunk_index,
                        "content_type": "MD",
                        "text_lenght_chars": len(node.text) 
                    }
                )
                chunks.append(node)


        return chunks


    def save(self, nodes: list) -> None:
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









        