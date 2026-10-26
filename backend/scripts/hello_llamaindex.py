"""Hello World LlamaIndex: Document -> Node -> VectorStoreIndex -> Retriever.

Chạy tại thư mục backend:  .\.venv\Scripts\python.exe scripts\hello_llamaindex.py
Chỉ cần JINA_API_KEY trong backend/.env; không dùng Chroma hay mô hình ngôn ngữ.
"""
import os

from dotenv import load_dotenv
from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.jinaai import JinaEmbedding

load_dotenv(".env")
Settings.embed_model = JinaEmbedding(api_key=os.environ["JINA_API_KEY"],
                                     model_name="jina-embeddings-v3")

docs = [
    Document(text="Sự nóng chảy là quá trình chuyển từ thể rắn sang thể lỏng.",
             metadata={"file_name": "demo.pdf", "page": 8}),
    Document(text="Nội năng của một vật là tổng động năng và thế năng của "
                  "các phân tử cấu tạo nên vật.",
             metadata={"file_name": "demo.pdf", "page": 11}),
]
nodes = SentenceSplitter(chunk_size=700, chunk_overlap=64).get_nodes_from_documents(docs)
index = VectorStoreIndex(nodes)

for r in index.as_retriever(similarity_top_k=2).retrieve("Thế nào là sự nóng chảy?"):
    print(f"{r.score:.3f} | trang {r.node.metadata['page']} | {r.node.text}")