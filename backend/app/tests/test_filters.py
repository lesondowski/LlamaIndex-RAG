"""Kiểm thử lọc metadata: logic dựng bộ lọc (thuần) và hành vi thật trên Chroma (tích hợp).

Phần tích hợp dùng Chroma tạm và embedding giả xác định, nên không cần khóa API hay mạng.
"""
import hashlib
import math

import chromadb
import pytest
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import FilterCondition, FilterOperator

import app.rag.indexing.index_manager as im
import app.rag.indexing.vector_store as vs
from app.rag.retrieval.filters import build_filters, resolve_scope
from app.rag.retrieval.retriever import Retriever
from app.rag.types import DocumentTags, MetadataError, QueryScope

A, B, C = "a" * 32, "b" * 32, "c" * 32   # Vật lý 12, Hóa học 12, Vật lý 11


# ----------------------------------------------------------------------------- phần thuần
def test_no_scope_or_unrestricted_scope_means_no_filter():
    assert build_filters(None) is None
    assert build_filters(QueryScope()) is None


def test_single_file_uses_eq_and_many_files_use_in():
    one = build_filters(QueryScope(file_ids=(A,)))
    many = build_filters(QueryScope(file_ids=(A, B)))
    assert [(f.key, f.operator, f.value) for f in one.filters] == [("file_id", FilterOperator.EQ, A)]
    assert [(f.key, f.operator, f.value) for f in many.filters] == [("file_id", FilterOperator.IN, [A, B])]
    assert one.condition == FilterCondition.AND


def test_all_conditions_are_combined_with_and():
    scope = QueryScope(file_ids=(A,), subject="vat_ly", grade=12, series="ket_noi_tri_thuc", page_from=2, page_to=4)
    built = build_filters(scope)
    got = {(f.key, f.operator) for f in built.filters}
    assert got == {
        ("file_id", FilterOperator.EQ), ("subject", FilterOperator.EQ), ("grade", FilterOperator.EQ),
        ("series", FilterOperator.EQ), ("page", FilterOperator.GTE), ("page", FilterOperator.LTE),
    }
    assert built.condition == FilterCondition.AND


def test_empty_file_ids_is_rejected_instead_of_searching_everything():
    with pytest.raises(MetadataError):
        resolve_scope(file_ids=[])
    with pytest.raises(MetadataError):
        build_filters(QueryScope(file_ids=()))


def test_resolve_scope_drops_deleted_files_and_dedupes():
    scope = resolve_scope(file_ids=[A, A, B], available_file_ids={A})
    assert scope.file_ids == (A,)
    with pytest.raises(MetadataError):
        resolve_scope(file_ids=[B], available_file_ids={A})


@pytest.mark.parametrize("bad_id", ["../../etc/passwd", "abc", "A" * 32, 123])
def test_resolve_scope_rejects_malformed_file_ids(bad_id):
    with pytest.raises(MetadataError):
        resolve_scope(file_ids=[bad_id])


def test_page_range_requires_exactly_one_file_and_ordered_bounds():
    assert resolve_scope(file_ids=[A], page_from=2, page_to=5).page_to == 5
    with pytest.raises(MetadataError):
        resolve_scope(file_ids=[A, B], page_from=1)
    with pytest.raises(MetadataError):
        resolve_scope(page_from=1)                        # không chọn sách nào
    with pytest.raises(MetadataError):
        resolve_scope(file_ids=[A], page_from=5, page_to=2)


@pytest.mark.parametrize("kwargs", [{"subject": "ma_thuat"}, {"grade": 13}, {"grade": 0}, {"series": "Có Dấu!"}])
def test_resolve_scope_rejects_values_outside_the_whitelist(kwargs):
    with pytest.raises(MetadataError):
        resolve_scope(**kwargs)


def test_document_tags_only_emit_keys_with_values():
    assert DocumentTags.from_input().to_metadata() == {}
    tags = DocumentTags.from_input(" Vat_Ly ", "12", "ket_noi_tri_thuc", "")
    assert tags.to_metadata() == {"subject": "vat_ly", "grade": 12, "series": "ket_noi_tri_thuc"}


# ----------------------------------------------------------------------------- phần tích hợp
class FakeEmbed(BaseEmbedding):
    """Vector xác định từ băm SHA-256: cùng chuỗi cho cùng vector; không cần mạng."""

    def _vec(self, text):
        raw = hashlib.sha256(text.encode()).digest()
        v = [b / 255 - 0.5 for b in raw[:16]]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def _get_query_embedding(self, q): return self._vec(q)
    async def _aget_query_embedding(self, q): return self._vec(q)
    def _get_text_embedding(self, t): return self._vec(t)


def make_nodes(file_id, name, pages, **tags):
    return [
        TextNode(text=f"{name} trang {page}: nội dung kiến thức số {page}",
                 metadata={"file_id": file_id, "file_name": name, "page": page, **tags})
        for page in range(1, pages + 1)
    ]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    collection = chromadb.PersistentClient(path=str(tmp_path / "chroma")).get_or_create_collection(
        "test", metadata={"hnsw:space": "cosine"})
    monkeypatch.setattr(vs, "get_collection", lambda: collection)
    monkeypatch.setattr(im, "get_embed_model", lambda: FakeEmbed())
    mgr = im.IndexManager()
    mgr.ingest_nodes(make_nodes(A, "Vật lý 12", 6, subject="vat_ly", grade=12, series="ket_noi_tri_thuc"), A, "Vật lý 12")
    mgr.ingest_nodes(make_nodes(B, "Hóa học 12", 4, subject="hoa_hoc", grade=12, series="ket_noi_tri_thuc"), B, "Hóa học 12")
    mgr.ingest_nodes(make_nodes(C, "Vật lý 11", 3, subject="vat_ly", grade=11, series="canh_dieu"), C, "Vật lý 11")
    return mgr


def files_of(results):
    return {r.node.metadata["file_id"] for r in results}


def test_no_scope_searches_every_book(manager):
    got = files_of(Retriever(manager).retrieve("kiến thức", top_k=10, scope=QueryScope()))
    assert got == {A, B, C}


def test_subject_filter_excludes_other_subjects(manager):
    results = Retriever(manager).retrieve("kiến thức", top_k=10, scope=resolve_scope(subject="vat_ly"))
    assert files_of(results) <= {A, C} and files_of(results)
    assert all(r.node.metadata["subject"] == "vat_ly" for r in results)


def test_subject_and_grade_narrow_to_one_book(manager):
    results = Retriever(manager).retrieve("kiến thức", top_k=10, scope=resolve_scope(subject="vat_ly", grade=12))
    assert files_of(results) == {A}


def test_series_filter(manager):
    results = Retriever(manager).retrieve("kiến thức", top_k=10, scope=resolve_scope(series="canh_dieu"))
    assert files_of(results) == {C}


def test_multiple_files_use_in(manager):
    results = Retriever(manager).retrieve("kiến thức", top_k=10, scope=resolve_scope(file_ids=[A, B]))
    assert files_of(results) == {A, B}


def test_page_range_inside_one_book(manager):
    scope = resolve_scope(file_ids=[A], page_from=2, page_to=4)
    results = Retriever(manager).retrieve("kiến thức", top_k=10, scope=scope)
    assert sorted(r.node.metadata["page"] for r in results) == [2, 3, 4]
    assert files_of(results) == {A}


def test_contradicting_conditions_return_nothing_instead_of_leaking(manager):
    scope = resolve_scope(file_ids=[B], subject="vat_ly")      # sách B là Hóa học
    assert Retriever(manager).retrieve("kiến thức", top_k=10, scope=scope) == []


def test_scope_and_file_ids_cannot_be_mixed(manager):
    with pytest.raises(ValueError):
        Retriever(manager).retrieve("kiến thức", file_ids=[A], scope=QueryScope())


def test_legacy_file_ids_path_still_works(manager):
    results = Retriever(manager).retrieve("kiến thức", top_k=10, file_ids=[C])
    assert files_of(results) == {C}


def test_invalid_scope_fails_before_touching_the_embedding_model(manager):
    with pytest.raises(MetadataError):
        manager.query("kiến thức", scope=QueryScope(file_ids=()))
