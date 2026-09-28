from app.rag.ingestion.loader import PDFLoader




def main():

    loader = PDFLoader()
    pdf_file = "app/database/data/BÁO CÁO TÀI CHÍNH VINAMILK 2025.pdf"
    split_file = "app/database/data/pdf/1.pdf"
#    pdf = loader.split_double_page_spread(input_pdf=pdf_file, output_pdf=split_file)
    document = loader.load(file_path=split_file)
    loader.PDFtoMarkdown(document,"app/database/data/markdown/markdown_new.md")


if __name__ ==  "__main__":
    main()