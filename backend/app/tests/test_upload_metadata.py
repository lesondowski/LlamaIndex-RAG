"""Kiểm thử nhãn sách ở route /upload: nhận, kiểm tra, lưu, trả về, và gắn vào Node."""
import json

import pytest
from fastapi.testclient import TestClient
from llama_index.core import Document

from app.main import app
from app.routers import upload

PDF = b"%PDF-1.4\n" + b"x" * 200


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    captured = {}

    def fake_ingest(pdf_path, file_id, display_name, tags=None):
        captured["tags"] = tags
        return 3

    monkeypatch.setattr(upload, "_ingest", fake_ingest)
    test_client = TestClient(app)
    test_client.captured = captured
    return test_client


def post(client, **form):
    return client.post("/upload", files={"file": ("sach.pdf", PDF, "application/pdf")}, data=form)


def test_upload_without_tags_still_works(client):
    response = post(client)
    assert response.status_code == 200
    assert client.captured["tags"].to_metadata() == {}
    assert "subject" not in response.json()


def test_upload_with_tags_saves_returns_and_lists_them(client, tmp_path):
    response = post(client, subject="vat_ly", grade="12", series="ket_noi_tri_thuc", volume="1")
    body = response.json()
    assert response.status_code == 200
    assert (body["subject"], body["grade"], body["series"], body["volume"]) == ("vat_ly", 12, "ket_noi_tri_thuc", 1)
    assert client.captured["tags"].to_metadata() == {"subject": "vat_ly", "grade": 12, "series": "ket_noi_tri_thuc", "volume": 1}

    saved = json.loads((tmp_path / f"{body['file_id']}.json").read_text(encoding="utf-8"))
    assert saved["subject"] == "vat_ly" and saved["grade"] == 12

    listed = client.get("/files").json()["files"]
    assert listed[0]["subject"] == "vat_ly" and listed[0]["grade"] == 12


@pytest.mark.parametrize("form", [{"subject": "ma_thuat"}, {"grade": "13"}, {"grade": "0"}, {"series": "Co Dau!"}, {"volume": "9"}])
def test_invalid_tags_are_rejected_before_anything_is_stored(client, tmp_path, form):
    response = post(client, **form)
    assert response.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_non_numeric_grade_is_rejected(client):
    assert post(client, grade="mười hai").status_code == 422


def test_tags_reach_every_node_without_none_values():
    documents = [Document(text="Định luật Ôm là...", metadata={"page": 2, "source": "x.pdf"})]
    tags = upload.DocumentTags.from_input(subject="vat_ly", grade=12)
    nodes = upload._build_nodes(documents, "a" * 32, "Vật lý 12.pdf", tags)
    assert nodes and all(n.metadata["subject"] == "vat_ly" and n.metadata["grade"] == 12 and n.metadata["page"] == 2 for n in nodes)
    assert all("series" not in n.metadata for n in nodes)          # khóa không có giá trị thì không có mặt
    plain = upload._build_nodes(documents, "a" * 32, "Vật lý 12.pdf")
    assert all("subject" not in n.metadata for n in plain)


def test_metadata_options_lists_the_whitelist(client):
    options = client.get("/metadata/options").json()
    assert "vat_ly" in options["subjects"] and options["grade"] == {"min": 1, "max": 12}


def test_build_nodes_splits_markdown_sections_and_preserves_pages_and_tags():
    documents = [
        Document(
            text="# Mục một\n\nNội dung thứ nhất.\n\n# Mục hai\n\nNội dung thứ hai.",
            metadata={"page": 1},
        ),
        Document(text="# Mục ba\n\nNội dung thứ ba.", metadata={"page": 2}),
    ]
    tags = upload.DocumentTags.from_input(subject="vat_ly", grade=12)

    nodes = upload._build_nodes(documents, "a" * 32, "Vật lý 12.pdf", tags)

    assert len(nodes) == 3
    assert [node.metadata["page"] for node in nodes] == [1, 1, 2]
    assert [node.metadata["chunk_index"] for node in nodes] == [0, 1, 2]
    for node in nodes:
        assert node.metadata["file_id"] == "a" * 32
        assert node.metadata["subject"] == "vat_ly"
        assert node.metadata["grade"] == 12
    assert "Nội dung thứ nhất." in nodes[0].text
    assert "Nội dung thứ hai." not in nodes[0].text
