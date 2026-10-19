from llama_index.core.schema import NodeWithScore


def build_citations(source_nodes: list[NodeWithScore]) -> list[dict]:
    citations = []
    for index, result in enumerate(source_nodes, start=1):
        node = result.node
        metadata = node.metadata or {}

        # Try to get citation_id and original_node_id from metadata, fallback to index and node.node_id
        citation_id = metadata.get("citation_id")
        original_node_id = metadata.get("original_node_id")
        if citation_id is None:
            citation_id = index
        if original_node_id is None:
            original_node_id = node.node_id

        file_id = metadata.get("file_id")
        source = metadata.get("file_name") or metadata.get("source")
        raw_page = metadata.get("page", metadata.get("page_label"))

        if not file_id:
            raise ValueError(f"Node {node.node_id} thiếu file_id.")
        if not source:
            raise ValueError(f"Node {node.node_id} thiếu source/file_name.")
        if raw_page is None:
            raise ValueError(f"Node {node.node_id} thiếu page.")

        try:
            citation_id = int(citation_id)
            page = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Metadata citation/page không hợp lệ ở node {node.node_id}."
            ) from exc

        if citation_id < 1 or page < 1:
            raise ValueError(
                f"Metadata citation/page phải lớn hơn 0 ở node {node.node_id}."
            )

        # Ưu tiên original_excerpt (text SẠCH); fallback về get_content() nếu thiếu
        excerpt = metadata.get("original_excerpt")
        if excerpt is None:
            excerpt = " ".join(node.get_content().split())[:2000]

        citations.append({
            "id": citation_id,
            "nodeId": original_node_id,
            "sourceId": file_id,
            "source": source,
            "page": page,
            "excerpt": excerpt,
            "score": result.score,
        })

    return sorted(citations, key=lambda citation: citation["id"])