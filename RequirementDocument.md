# BẢN YÊU CẦU KỸ THUẬT CHO TỪNG TẦNG VÀ QUY TRÌNH KHI CODING

## I. TỔNG QUAN CẤU TRÚC DỰ ÁN


### 1. Tổng quan công nghệ

- Công nghệ chính: ***LlamaIndex and RAG***

- Công nghệ và các thư viện khác: FastAPI, PostgreSQL + Chroma, SDK mô hình OpenAI/Gemini/Claude hoặc Ollama, 

- Backend: FastAPI

- ***Fontend***: (Phần này `Như` thêm vào giúp anh, làm phần nào giỏi nhất hoặc phù hợp nhất có thể)


### 2. Cấu trúc chức năng bắt buộc của hệ thống


- Trong hạng mục, hệ thống chia thành các khối: 

| Khối            | Cần hoàn thành                                                                     | Minh chứng                                                          |
| --------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Ingestion       | Nạp PDF/DOCX/TXT/Markdown, lưu metadata như tên file, trang, chương, loại tài liệu | Log ingestion, danh sách document đã nạp                            |
| Chunk/Node      | Chia tài liệu thành node/chunk, cấu hình chunk size và overlap                     | In ra vài node mẫu, giải thích vì sao chọn kích thước đó            |
| Embedding/Index | Tạo embedding và lưu vào vector store                                              | Có index lưu bền, không embedding lại toàn bộ mỗi lần chạy          |
| Retrieval       | Truy hồi top-k node theo câu hỏi                                                   | Hiển thị top-k retrieved nodes trước khi sinh câu trả lời           |
| Generation      | Dùng context được retrieve để sinh câu trả lời                                     | Prompt/context rõ ràng, có cơ chế không trả lời khi thiếu dữ liệu   |
| Citation        | Gắn nguồn cho câu trả lời                                                          | Trả về file, trang/chunk, đoạn nguồn                                |
| Evaluation      | Bộ câu hỏi cố định và bảng kết quả                                                 | Bảng precision/recall/hit-rate/faithfulness hoặc đánh giá grounding |
| Engineering     | Git, README, Docker, env, test, CI, log                                            | Repo chạy lại được, không lộ secret (phần này bắt buộc phải giấu được api key)                                 |


## II. CẤU TRÚC CÁC TẦNG VÀ CÁC YÊU CẦU


### 1. Tầng 1A — bắt buộc để được xem là làm đúng LlamaIndex/RAG

Các mục này phải có trước khi nghĩ tới hạng mức 3:

- Nạp dữ liệu bằng LlamaIndex, `không chỉ đọc file rồi dán nguyên vào prompt`.

- Có `Document/metadata`, ví dụ: file_name, page_label, section, source_type, created_at.


- Chia tài liệu thành `Node/chunk`, hiểu `chunk_size`, `chunk_overlap`.


- Embedding và lập chỉ mục, hiểu vector index lưu biểu diễn ngữ nghĩa để truy hồi.

- Retriever, biết top_k, similarity score, vì sao node được lấy.


- Response synthesis, dùng context retrieve để sinh câu trả lời.
Truy vết nguồn, câu trả lời phải biết lấy từ node/file/trang nào.


- Đánh giá thực nghiệm tối thiểu, không được chỉ demo vài câu rồi nói “đúng”.


### 2. Tầng 1B — chọn thêm để làm hệ thống có chiều sâu


| Mục chọn thêm                             |   Có nên làm không | Lý do                                                                |
| ----------------------------------------- | -----------------: | -------------------------------------------------------------------- |
| Persistent index                          |            Rất nên | Chứng minh hệ thống dùng được thực tế, không build lại index mỗi lần |
| Metadata filtering                        |            Rất nên | Ví dụ lọc theo môn học, chương, năm, loại tài liệu                   |
| Citation/source display                   |   Bắt buộc thực tế | Giúp chứng minh answer grounding                                     |
| Custom splitter/node parser               |                Nên | Tạo chiều sâu kỹ thuật rõ ràng                                       |
| Reranking                                 | Rất nên cho hạng 3 | Dễ đo trước/sau                                                      |
| Multiple collections/indexes              |                Nên | Ví dụ tài liệu môn học, quy chế, slide, bài tập                      |
| Conversation history/query transformation |         Có thể làm | Chỉ làm nếu có hỏi tiếp/follow-up                                    |
| Incremental re-index                      |                Nên | Thêm/xóa/cập nhật tài liệu mà không rebuild toàn bộ                  |



### 3. Tầng 2 — kỹ nghệ phần mềm bắt buộc


| Nhóm việc       | Cần hoàn thành                                                                           |
| --------------- | ---------------------------------------------------------------------------------------- |
| Git workflow    | Không commit thẳng vào `main`; có branch, PR/merge, commit rõ nghĩa                      |
| README          | Có hướng dẫn cài, chạy, ingest tài liệu, query, evaluation                               |
| Environment     | `.env.example`, không commit API key                                                     |
| Docker          | Nên có `docker compose` cho app + vector DB/Postgres                                     |
| Test            | Unit test cho chunking/retrieval helper; integration test cho API query; test evaluation |
| CI              | GitHub Actions chạy lint/test                                                            |
| API docs        | Nếu dùng FastAPI thì có endpoint rõ: upload, ingest, query, eval                         |
| Error handling  | File sai định dạng, query ngoài corpus, LLM timeout, vector DB lỗi                       |
| Logging         | Log request id, model, top-k, latency, retrieved sources                                 |
| Security cơ bản | Validate upload, giới hạn dung lượng file, CORS, rate limit, secret qua env              |


### 4. Tầng 3 — nâng cao, có thể làm hoặc không

| Loại câu hỏi                  | Mục đích                             |
| ----------------------------- | ------------------------------------ |
| Câu hỏi có trong tài liệu     | Đo retrieval/answer grounding        |
| Câu hỏi cần tổng hợp 2–3 đoạn | Đo khả năng retrieve nhiều node      |
| Câu hỏi nhiễu/gần giống       | Đo nhầm lẫn retrieval                |
| Câu hỏi ngoài corpus          | Kiểm tra hệ thống có biết từ chối    |
| Câu hỏi theo metadata         | Kiểm tra filter theo file/chương/năm |


- **Phần này sẽ bổ sung sau, tùy thuộc vào thời gian còn lại**




### III. QUY TRÌNH KHI CODING

| Nhóm                       | Yêu cầu                                                                                                                |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Không code demo hời hợt    | Không chỉ viết `VectorStoreIndex.from_documents()` rồi gọi `query_engine.query()` là xong                              |
| Có pipeline RAG rõ         | Code phải tách được các bước: ingest tài liệu → chunk/node → embedding → lưu vector → retrieve → synthesize → citation |
| Có metadata                | Mỗi chunk/node phải lưu nguồn: file, trang, vị trí, loại tài liệu, thời điểm ingest                                    |
| Có persistent vector store | Không build lại index mỗi lần chạy app                                                                                 |
| Có API rõ ràng             | Tối thiểu có upload/ingest/query/evaluate                                                                              |
| Có evaluation              | Có bộ câu hỏi kiểm thử cố định và script chạy đánh giá                                                                 |
| Có logging                 | Log query, top-k node, score, model, latency, token, lỗi                                                               |
| Có test                    | Unit test, integration test cho pipeline RAG, ít nhất vài case E2E  nếu test về giao diện hoặc test về backend thì có thể dùng nhiều testcase tùy ý, nhưng nếu test về RAG hoặc LlamaIndex thì yêu cầu phải sử dụng `ragas`                                                   |
| Có Docker                  | Phần này từ từ làm (chưa deploy nên chưa cần)                                                                                   |
| Không lộ secret            | API key, DB URL, model key phải nằm trong `.env`, không commit lên Git                                                 */




## BẮT BUỘC ĐỌC


- ***Yêu cầu bắt buộc: làm trong nhánh của mình, không làm trực tiếp lên main, commit rõ ràng, PR/merge, không lộ secret***

- ***Yêu cầu code mỗi ngày, hoặc rải rác trong tuần, không được code trước sau đó dồn hết vào 1 commit phần này nếu như dồn cả dự án vào 1 commit sau đó push vào tuần cuối cùng sẽ bị trừ điểm rất nặng***

- ***ĐOẠN NÀO CÓ SỬ DỤNG AI/VIBE CODE nhớ comment lại, sau này để có thể học hoặc note lại phần ứng dụng, sử dụng để làm gì***
