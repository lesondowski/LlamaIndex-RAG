from pathlib import Path
import json
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser,SentenceSplitter





class Chunking:
    
    def __init__(
        self,
        markdown_file: str | Path,
        output_file: str | Path,
        chunk_size: int = 550,
        chunk_overlap: int = 64,
    ) -> None:
        self.markdown_file = Path(markdown_file)
        self.output_file = Path(output_file)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def load_markdown(self) -> str:
        if not self.markdown_file.is_file():
            raise FileNotFoundError(self.markdown_file)

        return self.markdown_file.read_text(encoding="utf-8")


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
    def chunk(self):
        doc = self.create_document(self.load_markdown())

        sections = MarkdownNodeParser().get_nodes_from_documents([doc]) # chia theo heading

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



    def run(self) -> list:
        nodes = self.chunk()

        self.save(nodes)

        return nodes









        