from app.rag.ingestion.loader import PDFLoader
from app.rag.ingestion.chunker import Chunking




def main():
    Chunking(
        markdown_file="app/database/data/markdown/markdown_3.md",
        output_file = "app/database/data/nodes/nodes_1.json",
        chunk_size=512,
        chunk_overlap=64
    ).run()


#    loader = PDFLoader()
#    pdf_file = "app/database/data/VINAMILKPHANG.pdf"
#    split_file = "app/database/data/pdf/1.pdf"
#    pdf = loader.split_double_page_spread(input_pdf=pdf_file, output_pdf=split_file)
#    document = loader.load(file_path=split_file)
#    loader.PDFtoMarkdown(document,"app/database/data/markdown/markdown_3.md")


if __name__ ==  "__main__":
    main()

