from llama_index.core import Document

from app.rag.ingestion.chunker import Chunking


def make_page_document(page: int, marker: str) -> Document:
    repeated_content = " ".join([f"content_{marker}"] * 120)

    return Document(
        text=f"# Heading {marker}\n\n{marker}\n\n{repeated_content}",
        metadata={
            "document_id": "doc-test",
            "source": "/fixture/report.pdf",
            "file_name": "report.pdf",
            "page": page,
            "parser": "test",
        },
    )


def test_chunking_preserves_page_metadata_without_mixing_pages(tmp_path):
    documents = [
        make_page_document(1, "PAGE_ONE_ONLY"),
        make_page_document(2, "PAGE_TWO_ONLY"),
    ]
    chunker = Chunking(
        output_file=tmp_path / "nodes.json",
        chunk_size=50,
        chunk_overlap=8,
    )

    nodes = chunker.chunk(documents)

    assert nodes
    assert {node.metadata["page"] for node in nodes} == {1, 2}

    for node in nodes:
        metadata = node.metadata

        assert metadata["document_id"] == "doc-test"
        assert metadata["source"] == "/fixture/report.pdf"
        assert metadata["file_name"] == "report.pdf"
        assert metadata["parser"] == "test"
        assert metadata["page"] in {1, 2}
        assert node.text.strip()

        assert not (
            "PAGE_ONE_ONLY" in node.text and "PAGE_TWO_ONLY" in node.text
        )