from llama_index.core.schema import NodeWithScore
def build_citations(results: list[NodeWithScore]) -> list[dict]:
    citations = []
    for index, result in enumerate(results, start=1):
        node = result.node
        metadata = node.metadata or {}
        excerpt = " ".join(node.get_content().split())[:500]
        page = metadata.get("page", metadata.get("page_label"))

        if isinstance(page, str) and page.isdigit():
            page = int(page)

        citations.append(
            {
                "id": index,
                "sourceId": metadata.get("file_id"),
                "source": metadata.get("file_name")
                or metadata.get("source")
                or "Nguồn",
                "page": page,
                "excerpt": excerpt,
            }
        )

    return citations