/* ============================================================================
 * services/api.js — TẦNG GIAO TIẾP BACKEND/MOCK
 * ----------------------------------------------------------------------------
 * MỤC ĐÍCH: Đây là lớp DUY NHẤT trong toàn bộ app được phép "biết" backend là gì.
 *           UI (app.js) chỉ gọi window.api.* — không bao giờ gọi fetch/XHR trực tiếp.
 *
 * LỢI ÍCH:  Khi backend thay đổi (URL, format, mock→real), chỉ sửa file này.
 *           UI không cần đụng tới. Đây là Dependency Inversion trong thực tế.
 * ============================================================================
 *
 * HỢP ĐỒNG DỮ LIỆU (DATA CONTRACT) — 3 shape chính:
 *
 *   File     { id, name, size, type:'application/pdf',
 *              status:'uploading'|'processing'|'ready'|'error' }
 *            - id: ID phía client, sinh ngay khi user chọn file
 *            - serverId (không nằm trong shape gốc, UI tự thêm): ID backend trả về
 *
 *   Message  { id, role:'user'|'assistant', content,
 *              status:'pending'|'streaming'|'complete'|'error',
 *              citations:[{ id, sourceId, source, page, excerpt }] }
 *            - pending:   đã gửi, chưa có token đầu tiên
 *            - streaming: đang nhận token
 *            - complete:  xong (kể cả khi user bấm Dừng giữa chừng)
 *            - error:     lỗi hệ thống (KHÁC với "không tìm thấy")
 *
 *   Request  { message, sourceIds:[...] }
 *            - sourceIds rỗng = hỏi trên toàn bộ database chung
 *
 * HỢP ĐỒNG SSE CỦA CHAT (mỗi sự kiện là 1 dòng `data: {...}`):
 *   {event:'status',    message}       → tiến trình retrieval (Đang tìm…)
 *   {event:'citations', citations:[…]} → danh sách nguồn trích dẫn
 *   {event:'token',     content}       → 1 mảnh text của câu trả lời
 *   {event:'not_found', message}       → KHÔNG có tài liệu liên quan;
 *                                        message được hiển thị như MỘT câu trả lời
 *                                        (1 chunk, KHÔNG cần tách token)
 *   {event:'error',     message}       → lỗi hệ thống (KHÁC HẲN not_found)
 *
 * ABORT:    sendChat nhận h.signal (AbortSignal) để người dùng bấm Dừng.
 *           uploadFile trả về { promise, abort() } — UI chủ động hủy được.
 *
 * CHẾ ĐỘ:   USE_MOCK = true  → chạy hoàn toàn trên trình duyệt (không cần backend)
 *           USE_MOCK = false → gọi backend thật
 *
 * TÙY BIẾN:  Backend khác đường dẫn/định dạng → chỉ sửa EP + 2 hàm *Real.
 *           UI không đổi. Đây là lý do file này tồn tại.
 * ========================================================================== */

// Gán vào window.api — biến toàn cục để app.js có thể truy cập qua `api`
// Dùng IIFE (() => { ... })() để tạo scope riêng, không leak biến ra ngoài
window.api = (() => {

  /* --------------------------------------------------------------------------
   * CONFIG — Toàn bộ cấu hình tập trung 1 chỗ
   * -------------------------------------------------------------------------- */
  const CONFIG = {
    // Cờ quyết định dùng mock hay backend thật. Đây là "công tắc" duy nhất.
    USE_MOCK: false,
    // BASE: URL gốc của backend
    // - Nếu chạy qua HTTP/HTTPS (http:// hoặc https://) → dùng chính origin hiện tại
    //   VD: đang ở https://app.example.com → BASE = https://app.example.com
    // - Nếu mở file trực tiếp (file://) → fallback về localhost:8000
    //   (tránh lỗi khi mở index.html bằng double-click)
    BASE: location.port === '8000' ? location.origin : 'http://127.0.0.1:8000',

    // EP = Endpoint. Khi backend đổi path, CHỈ SỬA Ở ĐÂY.
    // chat: '/query/stream' trả về SSE (Server-Sent Events)
    EP: { upload: '/upload', files: '/files', chat: '/query/stream', health: '/health' },

    // Giới hạn kích thước file: 50 * 1024 * 1024 bytes = 50 MB
    MAX_FILE_SIZE: 50 * 1024 * 1024,

    // Số chunk retrieve tối đa từ vector DB — gửi lên backend trong body chat
    TOP_K: 5,
  };

  /* --------------------------------------------------------------------------
   * HELPERS — 3 hàm tiện ích dùng chung
   * -------------------------------------------------------------------------- */

  // sleep: Promise resolve sau `ms` milliseconds — dùng để giả lập delay mạng trong mock
  // Cách dùng: await sleep(500) → tạm dừng 500ms
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // fail: Tạo Error object có gắn thêm thuộc tính `kind`
  // - kind = 'network': lỗi mạng (DNS, CORS, mất kết nối)
  // - kind = 'server':  lỗi từ server (HTTP 4xx, 5xx)
  // UI dùng e.kind để phân biệt: network → báo "không kết nối được", server → báo "lỗi xử lý"
  const fail = (kind, msg) => Object.assign(new Error(msg), { kind });

  // abortErr: Tạo Error giả có name='AbortError' — giống error thật khi fetch bị hủy
  // Tại sao cần: UI check `e.name === 'AbortError'` để KHÔNG hiện toast lỗi
  // khi user chủ động bấm Dừng (khác với lỗi thật sự)
  const abortErr = () => Object.assign(new Error('aborted'), { name: 'AbortError' });

  /* --------------------------------------------------------------------------
   * validateFile — Kiểm tra file PDF qua 4 tầng
   * --------------------------------------------------------------------------
   * INPUT:  f — File object (từ <input type="file"> hoặc drag-drop)
   * OUTPUT: null nếu hợp lệ | { title, message } nếu lỗi
   *
   * TẠI SAO 4 TẦNG:
   *   Tầng 1+2 (đuôi + MIME): check nhanh, loại file .docx đổi tên thành .pdf
   *   Tầng 3 (kích thước):     loại file rỗng và file quá lớn (tốn băng thông)
   *   Tầng 4 (magic bytes):    kiểm tra THẬT — đọc 5 byte đầu, so sánh với '%PDF-'
   *                            Đây là cách duy nhất đáng tin ở client.
   *                            MIME có thể bị fake, nhưng magic bytes thì không.
   * -------------------------------------------------------------------------- */
  async function validateFile(f) {
    // Object lỗi mặc định cho trường hợp "không phải PDF"
    const bad = { title: 'File không hợp lệ', message: 'Chỉ hỗ trợ tài liệu PDF.' };

    // TẦNG 1 + 2: Kiểm tra đuôi file VÀ MIME type
    // - /\.pdf$/i.test(f.name): đuôi file phải là .pdf (không phân biệt hoa thường)
    // - f.type !== 'application/pdf': MIME browser khai báo phải đúng
    // Dùng || (OR) — chỉ cần MỘT trong hai fail là reject
    if (!/\.pdf$/i.test(f.name) || f.type !== 'application/pdf') return bad;

    // TẦNG 3a: File rỗng (0 byte) — không có nội dung để extract
    if (f.size === 0) return { title: 'File rỗng', message: 'File không có nội dung.' };

    // TẦNG 3b: File quá lớn — vượt MAX_FILE_SIZE
    // 1048576 = 1024 * 1024 (số byte trong 1 MB)
    if (f.size > CONFIG.MAX_FILE_SIZE) return {
      title: 'File quá lớn',
      message: 'Kích thước file không được vượt quá ' + CONFIG.MAX_FILE_SIZE / 1048576 + ' MB.'
    };

    // TẦNG 4: Magic bytes — đọc 5 byte đầu tiên và so sánh với '%PDF-'
    // - f.slice(0, 5): lấy 5 byte đầu (Blob.slice)
    // - .arrayBuffer(): chuyển Blob thành ArrayBuffer
    // - new TextDecoder().decode(...): decode ArrayBuffer thành string UTF-8
    // - '%PDF-' là chữ ký của mọi file PDF hợp lệ (PDF specification)
    // try/catch: arrayBuffer() có thể throw nếu file bị lock/đọc lỗi
    //            → bỏ qua kiểm tra (không reject) để tránh false negative
    try {
      if (new TextDecoder().decode(await f.slice(0, 5).arrayBuffer()) !== '%PDF-') return bad;
    } catch (_) { /* bỏ qua — không reject vì đọc lỗi */ }

    // null = hợp lệ. UI chỉ cần check `if (err) toast(...)`
    return null;
  }

  /* --------------------------------------------------------------------------
   * uploadMock — Giả lập upload file (không gọi backend)
   * --------------------------------------------------------------------------
   * INPUT:  file — File object
   *         { onProgress, onProcessing } — callbacks từ UI
   * OUTPUT: { promise: Promise<{id}>, abort: () => void }
   *
   * TIMELINE GIẢ LẬP:
   *   Progress 0 → 20 → 40 → 60 → 80 → 100 (mỗi bước delay 140ms)
   *   → onProcessing() (chuyển sang status 'processing')
   *   → delay 900ms (giả lập backend extract text + index)
   *   → resolve { id: 'file-xxxxx' }
   *
   * CƠ CHẾ ABORT: Biến closure `stop`. Khi UI gọi abort() → stop = true.
   *               Loop check `stop` mỗi iteration và throw AbortError.
   *               Đây là cooperative cancellation (không force kill).
   * -------------------------------------------------------------------------- */
  function uploadMock(file, { onProgress, onProcessing }) {
    // Biến closure — sống trong scope của uploadMock
    let stop = false;

    // Tạo async IIFE — chạy ngay lập tức, trả về Promise
    const promise = (async () => {
      // Loop từ 0 đến 80, mỗi bước +20 (0, 20, 40, 60, 80)
      for (let p = 0; p < 100; p += 20) {
        // Nếu user đã bấm Hủy → throw để thoát ngay
        if (stop) throw abortErr();
        // Báo % cho UI
        onProgress(p);
        // Delay 140ms giữa các bước (giả lập tốc độ mạng)
        await sleep(140);
      }
      // Sau loop, p = 100 (đã tăng nhưng chưa onProgress)
      onProgress(100);
      // Thông báo chuyển sang status 'processing' (backend đang xử lý)
      onProcessing();
      // Delay 900ms giả lập thời gian backend extract text + build index
      await sleep(900);
      // Check abort lần cuối trước khi resolve
      if (stop) throw abortErr();
      // Trả về ID giả: 'file-' + 6 ký tự random base36
      // Math.random().toString(36): chuyển số ngẫu nhiên thành base36 (0-9a-z)
      // .slice(2, 8): bỏ '0.' đầu, lấy 6 ký tự
      return { id: 'file-' + Math.random().toString(36).slice(2, 8) };
    })();

    // Trả về object giống interface của uploadReal
    // abort() chỉ set stop = true, không làm gì khác
    return { promise, abort: () => { stop = true; } };
  }

  /* --------------------------------------------------------------------------
   * uploadReal — Upload file thật lên backend
   * --------------------------------------------------------------------------
   * INPUT:  file, { onProgress, onProcessing }
   * OUTPUT: { promise: Promise<{id}>, abort: () => void }
   *
   * TẠI SAO DÙNG XMLHttpRequest THAY VÌ fetch:
   *   fetch KHÔNG CÓ upload progress event. Chỉ XHR mới có xhr.upload.onprogress.
   *   Đây là lý do kỹ thuật BẮT BUỘC, không phải sở thích.
   *
   * FLOW:
   *   fd.append → xhr.open → xhr.send
   *   ├─ upload.onprogress: báo % đã gửi lên server (bytes)
   *   ├─ upload.onload:     gửi xong bytes → gọi onProcessing() (server đang xử lý)
   *   ├─ xhr.onload:        server trả response → parse JSON
   *   ├─ xhr.onerror:       lỗi mạng (DNS, CORS, mất kết nối)
   *   └─ xhr.onabort:       user bấm Hủy → reject AbortError
   * -------------------------------------------------------------------------- */
  function uploadReal(file, { onProgress, onProcessing }) {
    // Tạo XHR object
    const xhr = new XMLHttpRequest();

    // Wrap trong Promise để UI dùng async/await được
    const promise = new Promise((res, rej) => {
      // Tạo FormData (multipart/form-data) — chuẩn để upload file
      const fd = new FormData();
      // Append file với key 'file' (backend phải đọc field này)
      fd.append('file', file);

      // Mở POST request tới endpoint upload
      xhr.open('POST', CONFIG.BASE + CONFIG.EP.upload);

      // Event: progress của quá trình UPLOAD (gửi bytes lên server)
      // - e.lengthComputable: browser biết tổng kích thước không?
      // - e.loaded / e.total: số bytes đã gửi / tổng bytes
      // - Math.round(... * 100): chuyển thành %
      xhr.upload.onprogress = e => e.lengthComputable && onProgress(Math.round(e.loaded / e.total * 100));

      // Event: upload bytes xong (server đã nhận đủ file, đang xử lý)
      // UI chuyển status từ 'uploading' → 'processing'
      xhr.upload.onload = () => onProcessing();

      // Event: server trả response
      xhr.onload = () => {
        // Parse JSON an toàn — nếu backend trả non-JSON thì j = {}
        let j = {};
        try { j = JSON.parse(xhr.responseText); } catch (_) {}

        // Check HTTP status code
        if (xhr.status >= 200 && xhr.status < 300) {
          // Thành công — chấp nhận 3 shape response:
          //   { file_id: 'xxx' }  ← FastAPI convention
          //   { id: 'xxx' }       ← shape phổ biến khác
          //   null                ← backend không trả ID (UI cần xử lý)
          res({ id: j.file_id ?? j.id ?? null });
        } else {
          // Lỗi HTTP — j.detail là convention của FastAPI
          rej(fail('server', 'HTTP ' + xhr.status + ' ' + (j.detail || '')));
        }
      };

      // Event: lỗi mạng (DNS fail, CORS block, mất kết nối giữa chừng)
      xhr.onerror = () => rej(fail('network', 'Network error'));

      // Event: user bấm Hủy → xhr.abort() → event này fire
      xhr.onabort = () => rej(abortErr());

      // Gửi request với body là FormData
      xhr.send(fd);
    });

    // Trả về object giống interface uploadMock
    // abort() gọi xhr.abort() → trigger onabort → reject AbortError
    return { promise, abort: () => xhr.abort() };
  }

  /* --------------------------------------------------------------------------
   * listFilesReal / deleteFileReal — đồng bộ panel "Nguồn" với danh sách thật
   * trên backend (GET/DELETE /files), thay vì chỉ sống trong state.files ở RAM.
   * -------------------------------------------------------------------------- */
  async function listFilesReal() {
    const res = await fetch(CONFIG.BASE + CONFIG.EP.files);
    if (!res.ok) throw fail('server', 'HTTP ' + res.status);
    const data = await res.json();
    return Array.isArray(data.files) ? data.files : [];
  }

  async function deleteFileReal(fileId) {
    const res = await fetch(CONFIG.BASE + CONFIG.EP.files + '/' + fileId, { method: 'DELETE' });
    if (res.status === 204 || res.ok) return;
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch (_) {}
    throw fail('server', 'HTTP ' + res.status + ' ' + detail);
  }

  /* --------------------------------------------------------------------------
   * DEMO_QA — Dữ liệu giả lập cho mock chat
   * --------------------------------------------------------------------------
   * Mỗi entry có:
   *   - re:    RegExp để match câu hỏi user
   *   - page:  số trang để hiện trong citation
   *   - ans:   câu trả lời (có markdown table + bold + [1] để test renderer)
   *   - snip:  excerpt cho citation panel
   *
   * 3 câu này được thiết kế để test 3 case UI KHÁC NHAU:
   *   - thanh toán: có TABLE + bold + chip [1]
   *   - ROE:        có bold + table, không có list
   *   - bò:         text thuần, không có markdown phức tạp
   * -------------------------------------------------------------------------- */
  const DEMO_QA = [
    {
      // Match câu hỏi chứa "thanh toán" hoặc "liquidity" (không phân biệt hoa thường)
      re: /thanh toán|liquidity/i,
      page: 11,
      // Câu trả lời có markdown: **bold**, [1] citation, table với header + separator
      ans: 'Hệ số thanh toán ngắn hạn năm 2025 là **1,96** (năm 2024: 2,03), hệ số thanh toán nhanh là **1,59** (năm 2024: 1,73) [1].\n\n| Chỉ tiêu | 2021 | 2022 | 2023 | 2024 | 2025 |\n|---|---|---|---|---|---|\n| Thanh toán ngắn hạn | 2,12 | 2,06 | 2,10 | 2,03 | 1,96 |\n| Thanh toán nhanh | 1,72 | 1,72 | 1,70 | 1,73 | 1,59 |',
      snip: 'Hệ số thanh toán ngắn hạn 2,12 2,06 2,10 2,03 1,96 · Hệ số thanh toán nhanh 1,72 1,72 1,70 1,73 1,59 (giai đoạn 2021-2025)'
    },
    {
      // Match "roe", "sinh lời", "lợi nhuận"
      re: /roe|sinh lời|lợi nhuận/i,
      page: 11,
      ans: '**ROE năm 2025 đạt 27,3%**, tăng so với 26,1% của năm 2024 [1].\n\n| Chỉ tiêu | 2021 | 2022 | 2023 | 2024 | 2025 |\n|---|---|---|---|---|---|\n| ROE (LNST/VCSH) | 29,7% | 26,1% | 25,8% | 26,1% | 27,3% |',
      snip: 'Hệ số LNST/Vốn chủ sở hữu (ROE) 29,7% 26,1% 25,8% 26,1% 27,3% (giai đoạn 2021-2025)'
    },
    {
      // Match "bò", "trang trại", "nguyên liệu"
      re: /bò|trang trại|nguyên liệu/i,
      page: 38,
      // Text thuần, không table/list — test renderer đơn giản
      ans: 'Vinamilk có đàn bò sữa trên 130.000 con, được nuôi tại 15 trang trại hiện đại, công nghệ cao trực thuộc Vinamilk và Mộc Châu Milk [1].',
      snip: 'Với đàn bò sữa trên 130.000 con đang được nuôi tại 15 trang trại hiện đại, công nghệ cao trực thuộc Vinamilk và Mộc Châu Milk'
    },
  ];

  /* --------------------------------------------------------------------------
   * chatMock — Giả lập chat với SSE streaming
   * --------------------------------------------------------------------------
   * INPUT:  { message, sourceIds } — request từ UI
   *         h — object chứa callbacks: onStatus, onCitations, onToken,
   *             onNotFound, sourceName, signal
   * OUTPUT: Promise<void> (resolve khi stream xong, reject khi abort/lỗi)
   *
   * FLOW GIẢ LẬP (giống backend RAG thật):
   *   1. Status "Đang tìm…"      → delay 600ms
   *   2. Status "Đang xếp hạng…" → delay 300ms
   *   3. Status "Đang tổng hợp…" → delay 300ms
   *   4. Nếu match DEMO_QA → gửi citations (retrieve xong mới generate)
   *   5. Nếu không match → gọi onNotFound() + gửi message như 1 chunk
   *   6. Stream từng từ của câu trả lời → delay 40ms/từ
   *
   * ABORT: `chk()` được gọi sau mỗi sleep. Nếu signal.aborted → throw AbortError.
   *        Đây là cách mock tôn trọng AbortSignal giống real.
   * -------------------------------------------------------------------------- */
  async function chatMock({ message, sourceIds }, h) {
    // Tìm entry trong DEMO_QA có regex match câu hỏi
    const hit = DEMO_QA.find(x => x.re.test(message));

    // sid = sourceId đầu tiên (nếu user đã chọn tài liệu), ngược lại null
    // ?? = nullish coalescing: chỉ fallback khi null/undefined (khác || — không fallback với 0, '')
    const sid = sourceIds[0] ?? null;

    // Tên nguồn hiển thị:
    // - Nếu hỏi trên DB chung (sid == null) → 'Dữ liệu có sẵn'
    // - Nếu có chọn tài liệu → gọi h.sourceName(sid) để lấy tên file
    //   fallback 'Báo cáo tài chính.pdf' nếu callback không trả gì
    const source = sid == null
      ? 'Dữ liệu có sẵn'
      : ((h.sourceName && h.sourceName(sid)) || 'Báo cáo tài chính.pdf');

    // Alias cho callbacks — viết ngắn gọn hơn, có guard `&&` tránh crash nếu callback undefined
    const st = t => h.onStatus && h.onStatus(t);

    // chk = checkpoint abort. Gọi sau mỗi await sleep.
    // Nếu user đã bấm Dừng → throw AbortError ngay, không stream tiếp.
    const chk = () => { if (h.signal && h.signal.aborted) throw abortErr(); };

    // BƯỚC 1: Status "Đang tìm…"
    // Text khác nhau tùy có chọn tài liệu hay không
    st('Đang tìm trong ' + (sourceIds.length ? sourceIds.length + ' tài liệu của bạn' : 'kho dữ liệu chung') + '…');
    await sleep(600);
    chk(); // check abort sau 600ms

    // BƯỚC 2: Status "Đang xếp hạng lại kết quả…"
    st('Đang xếp hạng lại kết quả…');
    await sleep(300);
    chk();

    // BƯỚC 3: Status "Đang tổng hợp câu trả lời…"
    st('Đang tổng hợp câu trả lời…');
    await sleep(300);
    chk();

    // BƯỚC 4: Nếu có kết quả → gửi citations TRƯỚC token
    // (giống backend thật: retrieve xong mới generate)
    if (hit) h.onCitations([
      {
        id: 1, sourceId: sid, source, page: hit.page,
        excerpt: hit.snip
      },
      {
        id: 2, sourceId: sid, source, page: hit.page + 1,
        excerpt: 'Đoạn liên quan khác được truy xuất từ tài liệu (nội dung minh họa).'
      }
    ]);

    // BƯỚC 5: Nếu KHÔNG match → báo notFound
    if (!hit && h.onNotFound) h.onNotFound();

    // Xây dựng câu trả lời:
    // - Nếu hit → dùng ans của DEMO_QA
    // - Nếu không → câu template "không tìm thấy"
    const ans = hit ? hit.ans : [
      'Tôi không tìm thấy thông tin liên quan trong dữ liệu hiện có. ' +
      'Bạn có thể thử diễn đạt lại câu hỏi cụ thể hơn, hoặc tải lên tài liệu chứa thông tin bạn cần.'
    ].join('\n');

    // BƯỚC 6: Gửi câu trả lời
    if (!hit) {
      // Case not_found: gửi NGUYÊN KHỐI qua 1 lần onToken
      // Comment giải thích: "khớp real: not_found + message = 1 chunk"
      // Lý do: backend thật khi không có tài liệu sẽ không chạy LLM,
      //        chỉ trả 1 câu template → không cần stream từng từ
      h.onToken(ans);
    } else {
      // Case có kết quả: stream từng từ, delay 40ms/từ
      // ans.split(' '): tách theo khoảng trắng
      // `w + ' '`: thêm lại khoảng trắng vì split đã bỏ
      for (const w of ans.split(' ')) {
        await sleep(40);
        chk(); // check abort giữa stream
        h.onToken(w + ' ');
      }
    }
  }

  /* --------------------------------------------------------------------------
   * normCites — Chuẩn hóa citation từ nhiều shape khác nhau
   * --------------------------------------------------------------------------
   * MỤC ĐÍCH: Backend (LlamaIndex, LangChain, custom) trả citation shape khác nhau.
   *           Hàm này map về 1 shape DUY NHẤT cho UI:
   *           { id, sourceId, source, page, excerpt }
   *
   * CHIẾN LƯỢC: Dùng ?? (nullish coalescing) để fallback qua nhiều field.
   *            ?? CHỈ fallback khi null/undefined — khác || (fallback cả 0, '')
   *
   * INPUT:  list — mảng citation từ backend (có thể không phải array)
   * OUTPUT: mảng citation đã chuẩn hóa
   * -------------------------------------------------------------------------- */
  const normCites = list => (Array.isArray(list) ? list : []).map((c, i) => {
    // n = node nếu có (LlamaIndex wrap trong node), ngược lại chính c
    // LlamaIndex: { node: { text, metadata }, score }
    const n = c.node || c;

    // m = metadata object (nếu có), ngược lại {}
    const m = n.metadata || {};

    // pg = page number, thử nhiều field theo thứ tự ưu tiên:
    //   1. m.page_label — LlamaIndex convention (string, VD: "11")
    //   2. m.page       — generic
    //   3. c.page       — top-level
    //   4. regex số từ c.article — fallback cuối (VD: "Article 11" → "11")
    //   (/\d+/.exec(...) || [])[0] — nếu không match thì [0] của [] = undefined
    const pg = m.page_label ?? m.page ?? c.page ?? (/\d+/.exec(c.article || '') || [])[0];

    // Trả về shape chuẩn hóa cho UI
    return {
      // id: đánh số lại từ 1 — BẤT KỂ backend trả gì
      // Lý do: UI dùng [1], [2] để map với chip inline. Nếu backend trả
      //        ID không tuần tự (VD: "chunk_abc") → vỡ mapping.
      id: i + 1,

      // sourceId: ID tài liệu (để UI group citations theo file)
      // Thử: c.sourceId → c.file_id → m.file_id → null
      sourceId: c.sourceId ?? c.file_id ?? m.file_id ?? null,

      // source: tên file/tài liệu hiển thị
      // Thử: c.source → c.title → m.file_name → m.title → 'Nguồn' (fallback)
      source: c.source ?? c.title ?? m.file_name ?? m.title ?? 'Nguồn',

      // page: số trang — convert sang Number nếu được
      // pg != null && pg !== '': chỉ xử lý khi có giá trị thật
      // Number(pg) || pg: nếu pg = "11" → 11; nếu pg = "trang 11" → giữ nguyên
      page: pg != null && pg !== '' ? (Number(pg) || pg) : null,

      // excerpt: đoạn trích — ép về String để tránh null/undefined
      // Thử: c.excerpt → c.snippet → c.text → n.text → ''
      excerpt: String(c.excerpt ?? c.snippet ?? c.text ?? n.text ?? '')
    };
  });

  /* --------------------------------------------------------------------------
   * readSSE — Đọc và parse Server-Sent Events stream
   * --------------------------------------------------------------------------
   * INPUT:  res — Response object từ fetch (đã có body stream)
   *         onEvent — callback được gọi mỗi khi parse xong 1 event
   * OUTPUT: Promise<void> (resolve khi stream kết thúc)
   *
   * TẠI SAO KHÔNG DÙNG EventSource:
   *   EventSource chỉ hỗ trợ GET. Chat cần POST (gửi body JSON).
   *   → Phải đọc stream thủ công qua fetch + ReadableStream.
   *
   * FORMAT SSE:
   *   data: {"event":"token","content":"Hệ "}\n\n
   *   data: {"event":"token","content":"số "}\n\n
   *   → Mỗi event cách nhau bằng 2 dòng trống (\n\n)
   *   → Dòng bắt đầu bằng "data:" chứa payload
   * -------------------------------------------------------------------------- */
  async function readSSE(res, onEvent) {
    // reader: đọc từng chunk từ response stream
    const reader = res.body.getReader();

    // dec: TextDecoder để chuyển Uint8Array → string UTF-8
    const dec = new TextDecoder('utf-8');

    // buf: buffer tích lũy dữ liệu chưa hoàn chỉnh
    let buf = '';

    // emit: xử lý 1 block (đã hoàn chỉnh, cách nhau bởi \n\n)
    const emit = block => {
      // Lấy tất cả dòng bắt đầu bằng "data:"
      // - block.split('\n'): tách thành các dòng
      // - .filter(l => l.startsWith('data:')): giữ dòng data
      // - .map(l => l.slice(5).trim()): bỏ "data:" (5 ký tự) + trim
      // - .join(''): nối lại (SSE cho phép nhiều dòng data)
      const data = block.split('\n')
        .filter(l => l.startsWith('data:'))
        .map(l => l.slice(5).trim())
        .join('');

      // Bỏ qua nếu data rỗng hoặc là sentinel [DONE] (OpenAI convention)
      if (!data || data === '[DONE]') return;

      // Parse JSON — nếu fail thì coi như token text thuần
      // (hữu ích khi backend trả text không phải JSON trong data:)
      let ev;
      try {
        ev = JSON.parse(data);
      } catch (_) {
        ev = { event: 'token', content: data };
      }

      // Gọi callback với event đã parse
      onEvent(ev);
    };

    // Vòng lặp đọc stream
    while (true) {
      // Đọc chunk tiếp theo — { done, value }
      // done = true khi stream kết thúc
      // value = Uint8Array (bytes)
      const { done, value } = await reader.read();

      // Stream kết thúc → thoát loop
      if (done) break;

      // Decode chunk với { stream: true } — QUAN TRỌNG:
      // Nếu 1 ký tự UTF-8 (VD: 'ệ') bị cắt giữa 2 chunk,
      // { stream: true } giữ state để decode đúng ở chunk sau.
      // .replace(/\r\n/g, '\n'): chuẩn hóa line ending (1 số server trả \r\n)
      buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');

      // Tách theo \n\n (2 dòng trống = kết thúc 1 event)
      const parts = buf.split('\n\n');

      // parts.pop(): phần tử CUỐI có thể CHƯA hoàn chỉnh → giữ lại trong buf
      // VD: buf = "event1\n\nevent2(đang dở)" → parts = ["event1", "event2(đang dở)"]
      //     → pop() lấy "event2(đang dở)" làm buf mới
      //     → forEach emit "event1"
      buf = parts.pop();

      // Emit các phần đã hoàn chỉnh
      parts.forEach(emit);
    }

    // Sau khi stream kết thúc: xử lý phần còn sót
    // (server có thể không kết thúc bằng \n\n)
    if (buf.trim()) emit(buf);
  }

  /* --------------------------------------------------------------------------
   * chatReal — Gọi backend thật qua fetch + SSE
   * --------------------------------------------------------------------------
   * INPUT:  { message, sourceIds }, h (callbacks)
   * OUTPUT: Promise<void>
   *
   * BODY GỬI LÊN:
   *   { question, top_k, file_ids? }
   *   - file_ids CHỈ gửi khi có sourceIds (dùng spread conditional)
   *   - Rỗng = hỏi trên DB chung, KHÔNG gửi field này
   *     (thay vì gửi [] — backend phân biệt được "không filter" vs "filter rỗng")
   * -------------------------------------------------------------------------- */
  async function chatReal({ message, sourceIds }, h) {
    let res;

    try {
      // POST request tới endpoint chat
      res = await fetch(CONFIG.BASE + CONFIG.EP.chat, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // signal: AbortSignal từ UI → fetch sẽ throw AbortError khi bị hủy
        signal: h.signal,
        // Body JSON:
        // - question: câu hỏi user (không phải 'message' — theo convention backend)
        // - top_k: số chunk retrieve (từ CONFIG)
        // - file_ids: CHỈ thêm khi sourceIds.length > 0
        //   Spread conditional: ...(cond ? {file_ids} : {}) — spread rỗng nếu cond false
        body: JSON.stringify({
          question: message,
          top_k: CONFIG.TOP_K,
          ...(sourceIds.length ? { file_ids: sourceIds } : {})
        })
      });
    } catch (e) {
      // fetch throw khi: mất mạng, CORS, HOẶC user bấm Dừng (AbortError)
      // - AbortError: giữ nguyên để UI không hiện toast lỗi
      // - Lỗi khác: gắn kind='network'
      throw e.name === 'AbortError' ? e : fail('network', 'fetch failed');
    }

    // Check HTTP status
    if (!res.ok) throw fail('server', 'HTTP ' + res.status);

    // Biến lưu lỗi từ SSE event 'error'
    // TẠI SAO KHÔNG THROW NGAY: throw giữa readSSE sẽ làm reader treo.
    // Cách này đảm bảo stream được đóng sạch trước khi throw.
    let err = '';

    // Đọc SSE stream
    await readSSE(res, j => {
      // Dispatch theo event type
      if (j.event === 'status') {
        // Fallback j.content nếu backend dùng field khác
        h.onStatus && h.onStatus(j.message || j.content || '');
      }
      else if (j.event === 'error') {
        // Lưu vào biến, KHÔNG throw ngay
        err = j.message || 'stream error';
      }
      else if (j.event === 'citations') {
        // Chuẩn hóa citation rồi gọi callback
        h.onCitations(normCites(j.citations));
      }
      else if (j.event === 'no_results' || j.event === 'not_found') {
        // Chấp nhận CẢ 2 tên event (backend có thể dùng tên khác nhau)
        h.onNotFound && h.onNotFound();
        // Nếu có message → gửi như 1 token (khớp mock)
        if (j.message) h.onToken(j.message);
      }
      else if (j.event === 'token') {
        h.onToken(j.content || '');
      }
    });

    // Sau khi stream kết thúc sạch → throw nếu có lỗi
    if (err) throw fail('server', err);
  }

  /* --------------------------------------------------------------------------
   * checkHealth — Ping backend để check kết nối
   * --------------------------------------------------------------------------
   * OUTPUT: Promise<boolean> — true nếu kết nối được
   *
   * LƯU Ý: Chỉ cần fetch KHÔNG throw là coi như "có kết nối"
   *        (bất kể HTTP status — kể cả 404 vẫn tính là connected)
   *        Vì mục đích là check "server có sống không", không phải check endpoint.
   * -------------------------------------------------------------------------- */
  async function checkHealth() {
    try {
      const res = await fetch(CONFIG.BASE + CONFIG.EP.health);
      // res.ok chỉ đúng với HTTP 2xx. Coi mọi phản hồi khác (kể cả 404) là "mất kết nối",
      // tránh huy hiệu báo "đã kết nối" nhầm khi CONFIG.BASE trỏ sai server.
      return res.ok;
    } catch (_) {
      // fetch ném lỗi khi mất mạng, server chưa chạy, hoặc bị CORS chặn.
      return false;
    }
  }
  /* --------------------------------------------------------------------------
   * PUBLIC API — Chỉ export 5 thứ ra ngoài
   * --------------------------------------------------------------------------
   * PATTERN: uploadFile / sendChat là WRAPPER — chọn impl dựa trên CONFIG.USE_MOCK.
   *          UI KHÔNG BAO GIỜ biết đang chạy mock hay real.
   *
   * HỆ QUẢ:  Đổi USE_MOCK = false → toàn bộ app chuyển sang backend thật,
   *          KHÔNG CẦN SỬA UI.
   * -------------------------------------------------------------------------- */
  return {
    // Export CONFIG để UI đọc USE_MOCK, MAX_FILE_SIZE, v.v.
    CONFIG,

    // Export validateFile — UI gọi trực tiếp (không phụ thuộc mock/real)
    validateFile,

    // Export checkHealth — UI gọi để hiển thị status badge
    checkHealth,

    // uploadFile: wrapper chọn mock/real
    // (f, cb) => (CONFIG.USE_MOCK ? uploadMock : uploadReal)(f, cb)
    // → Nếu USE_MOCK=true gọi uploadMock(f, cb)
    // → Nếu USE_MOCK=false gọi uploadReal(f, cb)
    uploadFile: (f, cb) => (CONFIG.USE_MOCK ? uploadMock : uploadReal)(f, cb),

    // sendChat: tương tự — wrapper chọn mock/real
    sendChat: (req, h) => (CONFIG.USE_MOCK ? chatMock : chatReal)(req, h),

    // listFiles: chế độ mock không có gì để liệt kê (không có backend lưu file thật)
    // → trả mảng rỗng, panel Nguồn coi như phiên mới, đúng hành vi mock từ trước giờ.
    listFiles: () => (CONFIG.USE_MOCK ? Promise.resolve([]) : listFilesReal()),

    // deleteFile: mock không cần gọi gì (file chỉ tồn tại ở RAM UI)
    deleteFile: fileId => (CONFIG.USE_MOCK ? Promise.resolve() : deleteFileReal(fileId))
  };
})();