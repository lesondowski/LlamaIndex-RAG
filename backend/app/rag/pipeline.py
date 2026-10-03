from app.rag.ingestion.loader import PDFLoader
from app.rag.ingestion.chunker import Chunking
from pathlib import Path



def main():
    BASE_DIR = Path(__file__).resolve().parents[2]

    PDF_PATH = BASE_DIR / "app/database/data/pdf/1.pdf" 
    MD_PATH = BASE_DIR / "app/database/data/markdown/1.md" 
    NODES_PATH = BASE_DIR / "app/database/data/nodes/nodes_1.json"


    loader = PDFLoader()
    documents = loader.load( str(PDF_PATH), document_id=PDF_PATH.stem, )


    loader.PDFtoMarkdown( documents, str(MD_PATH), )


    nodes = Chunking( output_file=NODES_PATH, chunk_size=550, chunk_overlap=64, ).run(documents)

    print(f"Documents: {len(documents)}")
    print(f"Nodes: {len(nodes)}")
    print(f"Markdown: {MD_PATH}")
    print(f"Nodes JSON: {NODES_PATH}")

if __name__ ==  "__main__":
    main()

