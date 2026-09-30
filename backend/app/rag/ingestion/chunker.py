from pathlib import Path
import json
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser,SentenceSplitter



MARKDOWN_FILE = "markdown/vinamilk.md"
OUTPUT_FILE = "output/nodes.json"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
class Chunking:

    def __init__(self,markdown_file=MARKDOWN_FILE,output_file=OUTPUT_FILE):
        self.markdown_file = markdown_file
        self.output_file = output_file


    def load_markdown(self, path):

        return Path(path).read_text(encoding="utf-8")

    def create_document(self, markdown):
        return Document(
            text=markdown,
            metadata={
                "source": "vinamilk_report.pdf",
                "document_type": "annual_report"
            }
        )


    def parse_markdown(self, doc):

        parser = MarkdownNodeParser()
        return parser.get_nodes_from_documents([doc])


    def split_large_nodes(self, nodes):
        splitter = SentenceSplitter(chunk_size=CHUNK_SIZE,chunk_overlap=CHUNK_OVERLAP)
        final_nodes = []
        
        for node in nodes:
            text = node.text
            # node nhỏ giữ nguyên
            if len(text) <= CHUNK_SIZE:
                final_nodes.append(node)
                continue


            docs = [Document(text=text,metadata=node.metadata)]
            chunks = splitter.get_nodes_from_documents(docs)
            final_nodes.extend(chunks)


        return final_nodes

    def enrich_metadata(self, nodes):

        for idx, node in enumerate(nodes):
            node.metadata.update(
                {
                    "node_id": idx,
                    "chunk_size": len(node.text),
                    "content_type": "text"
                }
            )
        return nodes

    def save_nodes(self, nodes, path):
        data = []
        for node in nodes:
            data.append(
                {
                    "id": node.node_id,
                    "text": node.text,
                    "metadata": node.metadata
                }
            )

        Path(path).parent.mkdir(exist_ok=True)
        with open(path,"w",encoding="utf-8") as f:

            json.dump(data,f,ensure_ascii=False,indent=2)

    def load(self):

        print("--- Loading Markdown ---")
        markdown = self.load_markdown(self.markdown_file)

        document = self.create_document(markdown)
        print("--- Parsing Markdown ---")

        nodes = self.parse_markdown(document)
        print("Initial nodes:",len(nodes))
        print("--- Chunking ---")

        nodes = self.split_large_nodes(nodes)
        print("Final nodes:",len(nodes))

        nodes = self.enrich_metadata(nodes)
        self.save_nodes(nodes,self.output_file)
        print("Saved:",self.output_file)

        return nodes
