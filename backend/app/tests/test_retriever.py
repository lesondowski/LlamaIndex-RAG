import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from app.rag.retrieval.retriever import Retriever


class FakeIndexManager:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, *, question, top_k, file_ids):
        self.calls.append(
            {
                "question": question,
                "top_k": top_k,
                "file_ids": file_ids,
            }
        )
        return self.results


def make_result() -> NodeWithScore:
    return NodeWithScore(
        node=TextNode(text="retrieved evidence"),
        score=0.9,
    )


def test_retriever_forwards_query_and_preserves_results():
    results = [make_result()]
    manager = FakeIndexManager(results)
    retriever = Retriever(index_manager=manager)

    actual = retriever.retrieve(
        question="  What does the report say?  ",
        top_k=3,
        file_ids=["file-a"],
    )

    assert actual is results
    assert manager.calls == [
        {
            "question": "What does the report say?",
            "top_k": 3,
            "file_ids": ["file-a"],
        }
    ]


def test_retriever_returns_empty_results_without_error():
    manager = FakeIndexManager([])
    retriever = Retriever(index_manager=manager)

    assert retriever.retrieve("question") == []


@pytest.mark.parametrize(
    ("question", "top_k"),
    [
        ("   ", 5),
        ("question", 0),
        ("question", 11),
        ("question", True),
    ],
)
def test_retriever_rejects_invalid_arguments(question, top_k):
    retriever = Retriever(index_manager=FakeIndexManager([]))

    with pytest.raises(ValueError):
        retriever.retrieve(question=question, top_k=top_k)


def test_retriever_rejects_blank_file_id():
    retriever = Retriever(index_manager=FakeIndexManager([]))

    with pytest.raises(ValueError):
        retriever.retrieve("question", file_ids=[""])