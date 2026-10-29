import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import chromadb
from llama_index.core.schema import MetadataMode
from llama_index.core.vector_stores.utils import metadata_dict_to_node

from app.rag.indexing import vector_store as vs
from app.rag.indexing.embeddings import get_embed_model


def restore_node(node_id, text, metadata, source):
    if not text or not text.strip():
        raise ValueError(f"Node {node_id} không có nội dung.")

    node = metadata_dict_to_node(dict(metadata), text=text)
    node.id_ = node_id
    node.embedding = None

    # Metadata ngoài cùng có thể đã được cập nhật sau khi serialize node.
    for key, value in metadata.items():
        if (
            not key.startswith("_")
            and key not in {"doc_id", "document_id", "ref_doc_id"}
        ):
            node.metadata[key] = value

    if (
        not node.metadata.get("file_id")
        or not node.metadata.get("file_name")
    ):
        raise ValueError(
            f"Node {node_id} thiếu file_id/file_name."
        )

    page = node.metadata.get("page")
    if type(page) is not int or page < 1:
        raise ValueError(
            f"Node {node_id} thiếu page nguyên dương."
        )

    fingerprint = hashlib.sha256(
        json.dumps(
            [source, node_id, text, metadata],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    node.metadata["_jina_migration_hash"] = fingerprint
    node.excluded_embed_metadata_keys = list(node.metadata)
    node.excluded_llm_metadata_keys = [
        key
        for key in node.metadata
        if key not in {"file_name", "page"}
    ]
    return node


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--file-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit phải lớn hơn 0.")
    if os.getenv("EMBED_PROVIDER", "").strip().lower() != "jina":
        parser.error("Đặt EMBED_PROVIDER=jina.")
    if args.source == vs.COLLECTION_NAME:
        parser.error("Collection nguồn và đích phải khác nhau.")

    client = chromadb.PersistentClient(path=str(vs.CHROMA_DIR))
    source = client.get_collection(
        args.source,
        embedding_function=None,
    )

    print(
        f"Source: {args.source}; target: {vs.COLLECTION_NAME}"
    )
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")

    embed = get_embed_model() if args.apply else None
    target = vs.get_collection() if args.apply else None
    store = vs.get_vector_store() if args.apply else None
    batch_size = embed.embed_batch_size if embed else 16
    where = {"file_id": args.file_id} if args.file_id else None
    scanned = added = skipped = 0

    while args.limit is None or scanned < args.limit:
        size = batch_size
        if args.limit is not None:
            size = min(size, args.limit - scanned)

        batch = source.get(
            limit=size,
            offset=scanned,
            where=where,
            include=["documents", "metadatas"],
        )
        ids = batch["ids"]
        if not ids:
            break

        nodes = [
            restore_node(node_id, text, metadata, args.source)
            for node_id, text, metadata in zip(
                ids,
                batch["documents"],
                batch["metadatas"],
                strict=True,
            )
        ]

        if args.apply:
            existing = target.get(
                ids=ids,
                include=["metadatas"],
            )
            hashes = {
                node_id: (metadata or {}).get(
                    "_jina_migration_hash"
                )
                for node_id, metadata in zip(
                    existing["ids"],
                    existing["metadatas"],
                    strict=True,
                )
            }

            pending = []
            for node in nodes:
                if node.node_id in hashes:
                    if hashes[node.node_id] != node.metadata[
                        "_jina_migration_hash"
                    ]:
                        raise ValueError(
                            f"Node {node.node_id} đã tồn tại "
                            "nhưng nội dung/nguồn khác."
                        )
                    skipped += 1
                else:
                    pending.append(node)

            if pending:
                vectors = embed.get_text_embedding_batch([
                    node.get_content(
                        metadata_mode=MetadataMode.NONE
                    )
                    for node in pending
                ])

                for node, vector in zip(
                    pending, vectors, strict=True
                ):
                    node.embedding = vector

                store.add(pending)
                added += len(pending)

        scanned += len(ids)
        print(
            f"Scanned={scanned}; added={added}; skipped={skipped}",
            flush=True,
        )

    if scanned == 0:
        raise ValueError(
            "Không tìm thấy node phù hợp trong collection nguồn."
        )

    print("Hoàn tất phạm vi đã chọn.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()