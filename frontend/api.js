/* ============================================================================
 * api.js — Tầng giao tiếp với backend (hoặc dữ liệu giả lập khi USE_MOCK = true)
 *
 * Mọi request HTTP của frontend đi qua file này; app.js chỉ gọi window.api.*.
 *   - Tài liệu: uploadFile, listFiles, deleteFile, getFileStatus
 *   - Hỏi đáp : sendChat — đọc luồng SSE: status → citations → token… | not_found | error
 *   - Kết nối : checkHealth
 *
 * Lỗi ném ra luôn có `kind`: 'network' (không tới được server) hoặc 'server'
 * (server trả lỗi) để app.js hiện thông báo phù hợp. Bấm dừng → AbortError.
 * [AI] File này viết với sự hỗ trợ của Claude Code.
 * ========================================================================== */

window.api = (() => {
  'use strict';

  const CONFIG = {
    USE_MOCK: false,
    // Mở trang từ chính backend (cổng 8000) thì gọi cùng origin; mở bằng Live Server thì gọi 127.0.0.1:8000.
    BASE: location.port === '8000' ? location.origin : 'http://127.0.0.1:8000',
    EP: { upload: '/upload', files: '/files', chat: '/query/stream', health: '/health' },
    MAX_FILE_SIZE: 50 * 1024 * 1024,   // khớp MAX_FILE_SIZE ở backend/app/config.py
    TOP_K: 5,                          // số đoạn sách truy hồi cho mỗi câu hỏi (backend cho phép 1–10)
  };

  // ==========================================================================
  // Tiện ích
  // ==========================================================================
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const fail = (kind, message) => Object.assign(new Error(message), { kind });
  const abortErr = () => Object.assign(new Error('aborted'), { name: 'AbortError' });

  /** FastAPI trả `detail` là chuỗi (HTTPException) hoặc mảng {msg, loc, ...} (lỗi validate 422). */
  function detailOf(body) {
    const detail = body && body.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => item.msg).filter(Boolean).join('; ');
    return '';
  }

  async function errorFrom(res) {
    let detail = '';
    try { detail = detailOf(await res.json()); } catch (_) { /* body không phải JSON */ }
    return fail('server', detail || `HTTP ${res.status}`);
  }

  // ==========================================================================
  // Kiểm tra file trước khi tải lên (server vẫn kiểm tra lại)
  // ==========================================================================
  /** Trả null nếu hợp lệ, hoặc { title, message } để hiện thông báo. */
  async function validateFile(file) {
    const notPdf = { title: 'File không hợp lệ', message: 'Chỉ hỗ trợ tài liệu PDF.' };

    if (!/\.pdf$/i.test(file.name)) return notPdf;
    if (file.type && file.type !== 'application/pdf') return notPdf;
    if (file.size === 0) return { title: 'File rỗng', message: 'File không có nội dung.' };
    if (file.size > CONFIG.MAX_FILE_SIZE) {
      return {
        title: 'File quá lớn',
        message: `Kích thước file không được vượt quá ${CONFIG.MAX_FILE_SIZE / 1048576} MB.`,
      };
    }

    // Đuôi .pdf có thể giả mạo → đọc 5 byte đầu (magic bytes) như backend.
    try {
      const head = new TextDecoder().decode(await file.slice(0, 5).arrayBuffer());
      if (head !== '%PDF-') return notPdf;
    } catch (_) { /* không đọc được thì để server quyết định */ }

    return null;
  }

  // ==========================================================================
  // Tài liệu
  // ==========================================================================
  /** Upload bằng XHR (fetch chưa báo được tiến độ gửi). Trả { promise, abort }. */
  function uploadReal(file, { onProgress, onProcessing }) {
    const xhr = new XMLHttpRequest();
    const promise = new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('file', file);

      xhr.open('POST', CONFIG.BASE + CONFIG.EP.upload);
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.upload.onload = () => onProcessing();   // gửi xong, server bắt đầu xử lý
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (_) { /* phản hồi rỗng */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);   // { file_id, status, message }
        else reject(fail('server', detailOf(body) || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(fail('network', 'Không thể kết nối máy chủ.'));
      xhr.onabort = () => reject(abortErr());
      xhr.send(form);
    });
    return { promise, abort: () => xhr.abort() };
  }

  async function listFilesReal() {
    const res = await fetch(CONFIG.BASE + CONFIG.EP.files);
    if (!res.ok) throw await errorFrom(res);
    const data = await res.json();
    return Array.isArray(data.files) ? data.files : [];
  }

  async function deleteFileReal(fileId) {
    const res = await fetch(`${CONFIG.BASE}${CONFIG.EP.files}/${encodeURIComponent(fileId)}`, { method: 'DELETE' });
    // 404 = server không còn file (vd. xử lý lỗi nên backend đã tự dọn) → coi như đã xóa,
    // để người dùng gỡ được mục lỗi khỏi danh sách thay vì bấm X mãi không được.
    if (res.ok || res.status === 404) return;
    throw await errorFrom(res);
  }

  /** Trạng thái xử lý ngầm sau khi upload: { status: processing|indexed|error, progress, message }. */
  async function getFileStatusReal(fileId) {
    const res = await fetch(`${CONFIG.BASE}${CONFIG.EP.files}/${encodeURIComponent(fileId)}/status`);
    if (!res.ok) throw await errorFrom(res);
    return res.json();
  }

  // ==========================================================================
  // Hỏi đáp qua Server-Sent Events
  // ==========================================================================
  /** Chuẩn hóa trích dẫn từ backend (citations.py) về đúng dạng giao diện cần. */
  const normCites = list => (Array.isArray(list) ? list : []).map((c, i) => ({
    id: Number.isFinite(+c.id) ? +c.id : i + 1,
    sourceId: c.sourceId ?? null,
    source: c.source || 'Nguồn',
    page: c.page ?? null,
    excerpt: String(c.excerpt ?? ''),
  }));

  /**
   * Đọc luồng SSE: mỗi sự kiện là một hoặc nhiều dòng "data: <json>" kết thúc bằng dòng trống.
   * Mạng có thể cắt giữa một sự kiện → giữ phần chưa trọn trong `buffer` chờ lần đọc sau.
   */
  async function readSSE(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    const emit = block => {
      const data = block.split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trim())
        .join('');
      if (!data || data === '[DONE]') return;
      try { onEvent(JSON.parse(data)); }
      catch (_) { onEvent({ event: 'token', content: data }); }
    };

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop();
      blocks.forEach(emit);
    }
    if (buffer.trim()) emit(buffer);
  }

  /**
   * Gửi câu hỏi và gọi các hook theo từng sự kiện.
   * @param {{message: string, sourceIds: string[], history?: {role: string, content: string}[]}} req
   *   history: các lượt hỏi–đáp TRƯỚC câu này — server dùng để viết lại câu hỏi nối tiếp.
   * @param {{signal, onStatus, onCitations, onToken, onNotFound}} hooks
   */
  async function chatReal({ message, sourceIds, history }, hooks) {
    let res;
    try {
      res = await fetch(CONFIG.BASE + CONFIG.EP.chat, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: hooks.signal,
        body: JSON.stringify({
          question: message,
          top_k: CONFIG.TOP_K,
          ...(sourceIds.length ? { file_ids: sourceIds } : {}),   // không chọn = mọi sách đã tải lên
          ...(history && history.length ? { history } : {}),
        }),
      });
    } catch (e) {
      throw e.name === 'AbortError' ? e : fail('network', 'Không thể kết nối máy chủ.');
    }
    if (!res.ok) throw await errorFrom(res);

    let streamError = '';
    await readSSE(res, event => {
      switch (event.event) {
        case 'status':
          hooks.onStatus?.(event.message || '');
          break;
        case 'citations':
          hooks.onCitations(normCites(event.citations));
          break;
        case 'token':
          hooks.onToken(event.content || '');
          break;
        case 'not_found':
          hooks.onNotFound?.();
          if (event.message) hooks.onToken(event.message);
          break;
        case 'error':
          streamError = event.message || 'Không thể xử lý câu hỏi lúc này.';
          break;
        default:
          break;
      }
    });
    if (streamError) throw fail('server', streamError);
  }

  async function checkHealth() {
    try {
      return (await fetch(CONFIG.BASE + CONFIG.EP.health)).ok;
    } catch (_) {
      return false;
    }
  }

  // ==========================================================================
  // Chế độ giả lập (USE_MOCK = true) — demo giao diện khi không chạy backend
  // ==========================================================================
  function uploadMock(file, { onProgress, onProcessing }) {
    let stopped = false;
    const promise = (async () => {
      for (let p = 0; p <= 100; p += 20) {
        if (stopped) throw abortErr();
        onProgress(p);
        await sleep(140);
      }
      onProcessing();
      await sleep(900);
      if (stopped) throw abortErr();
      return { file_id: 'mock-' + Math.random().toString(36).slice(2, 10) };
    })();
    return { promise, abort: () => { stopped = true; } };
  }

  const DEMO_QA = [
    {
      match: /toán|hình tròn|diện tích|phương trình|bán kính/i,
      page: 45,
      answer: 'Diện tích hình tròn được tính bằng công thức $S = \\pi r^2$, trong đó $r$ là bán kính [1].\n\nChu vi hình tròn là $C = 2\\pi r$ [1].',
      excerpt: 'Diện tích hình tròn được tính bằng công thức S = πr². Chu vi hình tròn là C = 2πr.',
    },
    {
      match: /lý|vận tốc|ohm|điện trở|cường độ/i,
      page: 112,
      answer: 'Định luật Ohm: cường độ dòng điện chạy qua dây dẫn tỉ lệ thuận với hiệu điện thế và tỉ lệ nghịch với điện trở của dây [1].\n\n| Đại lượng | Ký hiệu | Đơn vị |\n|---|---|---|\n| Hiệu điện thế | U | Vôn (V) |\n| Cường độ dòng điện | I | Ampe (A) |\n| Điện trở | R | Ôm (Ω) |',
      excerpt: 'Công thức định luật Ohm: I = U / R. Trong đó U là hiệu điện thế, I là cường độ dòng điện, R là điện trở.',
    },
    {
      match: /văn|chí phèo|nam cao/i,
      page: 28,
      answer: 'Tác phẩm **Chí Phèo** của Nam Cao kể về bi kịch người nông dân bị tha hóa trong xã hội cũ [1].',
      excerpt: 'Chí Phèo là kiệt tác của Nam Cao, phản ánh hiện thực xã hội Việt Nam trước Cách mạng tháng Tám.',
    },
  ];

  async function chatMock({ message, sourceIds }, hooks) {
    const hit = DEMO_QA.find(item => item.match.test(message));
    const sourceId = sourceIds[0] ?? null;
    const source = (sourceId && hooks.sourceName?.(sourceId)) || 'Sách giáo khoa (demo).pdf';
    const checkAbort = () => { if (hooks.signal?.aborted) throw abortErr(); };

    hooks.onStatus?.('Đang truy hồi nội dung liên quan…');
    await sleep(600); checkAbort();
    hooks.onStatus?.('Đang tổng hợp câu trả lời…');
    await sleep(400); checkAbort();

    if (!hit) {
      hooks.onNotFound?.();
      hooks.onToken('Không tìm thấy thông tin liên quan trong phạm vi đã chọn.');
      return;
    }
    hooks.onCitations([{ id: 1, sourceId, source, page: hit.page, excerpt: hit.excerpt }]);
    for (const word of hit.answer.split(' ')) {
      await sleep(35); checkAbort();
      hooks.onToken(word + ' ');
    }
  }

  // ==========================================================================
  // Public API
  // ==========================================================================
  const mock = CONFIG.USE_MOCK;
  return {
    CONFIG,
    validateFile,
    checkHealth,
    uploadFile: (file, hooks) => (mock ? uploadMock : uploadReal)(file, hooks),
    listFiles: () => (mock ? Promise.resolve([]) : listFilesReal()),
    deleteFile: fileId => (mock ? Promise.resolve() : deleteFileReal(fileId)),
    getFileStatus: fileId => (mock
      ? Promise.resolve({ status: 'indexed', progress: 100, message: 'Sẵn sàng (demo)' })
      : getFileStatusReal(fileId)),
    sendChat: (req, hooks) => (mock ? chatMock : chatReal)(req, hooks),
  };
})();
