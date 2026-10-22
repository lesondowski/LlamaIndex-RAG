import re

from llama_index.core.schema import NodeWithScore

EXCERPT_CHARS = 1200


def clean_excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """[AI] Đoạn trích hiển thị ở panel "Trích dẫn từ sách".

    GIỮ xuống dòng: PDF được LlamaParse đọc thành markdown, bảng / danh sách / tiêu đề phải đúng
    từng dòng mới hiển thị được (trước đây gộp hết thành một dòng → bảng thành chuỗi
    "| x | t | --- | 0 | 25 |" dính vào tiêu đề). Chỉ gọn khoảng trắng trong từng dòng, cắt ở
    ranh giới dòng (không cắt đôi hàng của bảng) và không cắt dở công thức $...$.
    """
    lines = [" ".join(line.split()) for line in str(text).splitlines()]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(out) <= limit:
        return out
    cut = out.rfind("\n", 0, limit)
    if cut < limit // 2:            # dòng cuối quá dài → cắt ở ranh giới từ
        cut = out.rfind(" ", 0, limit)
    out = out[:cut if cut > 0 else limit].rstrip()
    for mark in ("$$", "$"):        # số dấu $ lẻ = đang cắt giữa công thức → lùi về trước công thức đó
        if out.count(mark) % 2:
            out = out[:out.rfind(mark)].rstrip()
    return out


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
            excerpt = clean_excerpt(node.get_content())

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