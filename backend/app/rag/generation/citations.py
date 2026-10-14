from llama_index.core.schema import NodeWithScore
def build_citations(results: list[NodeWithScore]) -> list[dict]:
    citations = []
    for index, result in enumerate(results, start=1):
        node = result.node
        metadata = node.metadata or {}
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
            page = int(raw_page)
        except(TypeError, ValueError) as exc:
            raise ValueError(f"Node - {node.node_id} - có page không hợp lệ: {raw_page!r}") from exc
        if page < 1:
            raise ValueError(f"Error page < 1: Node {node.node_id} có page không hợp lệ: {page}")
        citations.append({
            "id": index,
            "nodeId": node.node_id,
            "sourceId": file_id,
            "source": source,
            "page": page,
            "excerpt": " ".join(node.get_content().split())[:500],
            "score": result.score,
        })

    return citations