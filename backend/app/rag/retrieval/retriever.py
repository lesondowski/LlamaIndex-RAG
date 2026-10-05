from llama_index.core.schema import NodeWithScore
from app.rag.indexing.index_manager import get_index_manager


class retriver():
    def retrieve(question: str, top_k: int = 5, file_ids: list[str] | None = None) -> list[NodeWithScore]:
        return get_index_manager().query(question=question, top_k=top_k, file_ids=file_ids)



