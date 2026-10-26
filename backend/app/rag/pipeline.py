import os
from pathlib import Path
from dotenv import load_dotenv
from app.rag.ingestion.chunker import CHUNK_OVERLAP, CHUNK_SIZE, Chunking
from app.rag.ingestion.loader import PDFLoader
from app.rag.indexing.index_manager import get_index_manager

def loading(input_pdf: str, output_markdown:str, file_id: str):
    print("-----    PARSE PDF WITH LLAMACLOUD   ------")
    loader = PDFLoader()
    documents = loader.load_llama_parse(path_pdf=str(input_pdf),markdown_output=str(output_markdown),document_id=file_id,)
    if not documents:
        raise RuntimeError("LlamaParse không trả về Document nào.")
    print(f"Page documents: {len(documents)}")
    print(f"Markdown: {output_markdown}")

    return documents


def chunk(documents, output_nodes, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    print("---------    CHUNK MARKDOWN DOCUMENTS   --------")
    chunker = Chunking(
        output_file=output_nodes,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    nodes = chunker.chunk(documents)
    if not nodes:
        raise RuntimeError("Không tạo được node nào từ PDF.")
    print(f"Nodes: {len(nodes)}")
    print(f"Sample node page: {nodes[0].metadata.get('page')}")
    print(f"Sample node text: {nodes[0].text[:300]}")
    chunker.save(nodes)
    print(f"Nodes JSON: {output_nodes}")
    return nodes, chunker

def embedding(nodes, chunker, file_id, input_pdf):
    print("-------   EMBED AND INDEX INTO CHROMA    ---------")
    manager = get_index_manager()
    indexed_count = manager.ingest_nodes(
        nodes,
        file_id=file_id,
        file_name=input_pdf.name,
    )
    chunker.save(nodes)
    print(f"Indexed nodes: {indexed_count}")
    print(f"Chroma file_id: {file_id}")
    return manager


def retrieval(manager, file_id):
    question = "Nội dung chính của tài liệu là gì?"
    results = manager.query(
        question=question,
        top_k=3,
        file_ids=[file_id],
    )
    print(f"Question: {question}")
    print(f"Retrieved nodes: {len(results)}")
    for index, result in enumerate(results, start=1):
        node = result.node 
        print() 
        print("-" * 70) 
        print(f"TOP {index}") 
        print("-" * 70) 
        print(f"Score: {result.score}")
        print(f"Node ID: {node.node_id}") 
        print(f"File: {node.metadata.get('file_name')}") 
        print(f"Page: {node.metadata.get('page')}")
        print("\nContent:")
        print(node.text[:1000])



def main() -> None:
    load_dotenv()
    #Setup Parameter
    folder_pdf = Path("./app/database/data/pdf/")
    output_dir =  Path("./app/database/storage/pipeline_runs")
    file_ids = 1
    pdf_files = sorted(
        f for f in folder_pdf.rglob("*")
        if f.is_file() and f.suffix.lower() == ".pdf"
    )
    for pdf in pdf_files:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_markdown = (output_dir / f"{pdf.stem}.md")
        output_nodes = (output_dir / f"{pdf.stem}_nodes.json")
        print("_" * 70)
        print("PDF:", pdf.stem)
        print("File ID:", file_ids)
        print("Markdown:", output_markdown)
        print("Nodes:", output_nodes)
        print("_" * 70)
        file_ids = str(file_ids)
        documents = loading(input_pdf=pdf, output_markdown=output_markdown, file_id=file_ids)
        nodes, chunker = chunk(documents=documents, output_nodes=output_nodes)
        mnr = embedding(nodes=nodes, chunker=chunker, file_id=file_ids, input_pdf=pdf)
        retrieval(manager=mnr, file_id=file_ids)
        file_ids = int(file_ids)
        file_ids = file_ids + 1




if __name__ == "__main__":
    main()