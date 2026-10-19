/* ============================================================================
 * services/api.js — TẦNG GIAO TIẾP BACKEND/MOCK
 * ========================================================================== */

window.api = (() => {

  const CONFIG = {
    USE_MOCK: false,
    BASE: location.port === '8000' ? location.origin : 'http://127.0.0.1:8000',
    EP: { upload: '/upload', files: '/files', chat: '/query/stream', health: '/health' },
    MAX_FILE_SIZE: 50 * 1024 * 1024,
    TOP_K: 5,
  };

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const fail = (kind, msg) => Object.assign(new Error(msg), { kind });
  const abortErr = () => Object.assign(new Error('aborted'), { name: 'AbortError' });

  /* --------------------------------------------------------------------------
   * validateFile — Nới lỏng MIME check (tin tưởng vào magic bytes)
   * -------------------------------------------------------------------------- */
  async function validateFile(f) {
    const bad = { title: 'File không hợp lệ', message: 'Chỉ hỗ trợ tài liệu PDF.' };

    if (!/\.pdf$/i.test(f.name)) return bad;
    if (f.type && f.type !== 'application/pdf') return bad;

    if (f.size === 0) return { title: 'File rỗng', message: 'File không có nội dung.' };
    if (f.size > CONFIG.MAX_FILE_SIZE) return {
      title: 'File quá lớn',
      message: 'Kích thước file không được vượt quá ' + CONFIG.MAX_FILE_SIZE / 1048576 + ' MB.'
    };

    try {
      if (new TextDecoder().decode(await f.slice(0, 5).arrayBuffer()) !== '%PDF-') return bad;
    } catch (_) { }

    return null;
  }

  function uploadMock(file, { onProgress, onProcessing }) {
    let stop = false;
    const promise = (async () => {
      for (let p = 0; p < 100; p += 20) {
        if (stop) throw abortErr();
        onProgress(p);
        await sleep(140);
      }
      onProgress(100);
      onProcessing();
      await sleep(900);
      if (stop) throw abortErr();
      return { file_id: 'file-' + Math.random().toString(36).slice(2, 8) }; // Đổi id thành file_id cho đồng bộ
    })();
    return { promise, abort: () => { stop = true; } };
  }

  function uploadReal(file, { onProgress, onProcessing }) {
    const xhr = new XMLHttpRequest();
    const promise = new Promise((res, rej) => {
      const fd = new FormData();
      fd.append('file', file);
      xhr.open('POST', CONFIG.BASE + CONFIG.EP.upload);
      xhr.upload.onprogress = e => e.lengthComputable && onProgress(Math.round(e.loaded / e.total * 100));
      xhr.upload.onload = () => onProcessing();
      xhr.onload = () => {
        let j = {};
        try { j = JSON.parse(xhr.responseText); } catch (_) { }
        if (xhr.status >= 200 && xhr.status < 300) {
          // Backend trả về { file_id, status, message }
          res(j);
        } else {
          rej(fail('server', 'HTTP ' + xhr.status + ' ' + (j.detail || '')));
        }
      };
      xhr.onerror = () => rej(fail('network', 'Network error'));
      xhr.onabort = () => rej(abortErr());
      xhr.send(fd);
    });
    return { promise, abort: () => xhr.abort() };
  }

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
    try { detail = (await res.json()).detail || ''; } catch (_) { }
    throw fail('server', 'HTTP ' + res.status + ' ' + detail);
  }

  /* --------------------------------------------------------------------------
   * getFileStatusReal — API mới phục vụ Polling tiến trình ngầm
   * -------------------------------------------------------------------------- */
  async function getFileStatusReal(fileId) {
    const res = await fetch(`${CONFIG.BASE}${CONFIG.EP.files}/${fileId}/status`);
    if (!res.ok) throw fail('server', 'HTTP ' + res.status);
    return await res.json(); // Trả về { status, progress, message }
  }

  /* --------------------------------------------------------------------------
   * DEMO_QA — Dữ liệu giả lập cho môn Toán, Lý, Văn (SGK)
   * -------------------------------------------------------------------------- */
  const DEMO_QA = [
    {
      re: /toán|hình tròn|diện tích|phương trình|bán kính/i,
      page: 45,
      ans: 'Công thức tính diện tích hình tròn là \\(S = \\pi r^2\\), trong đó \\(r\\) là bán kính của hình tròn [1].\n\nNgoài ra, chu vi hình tròn được tính bằng công thức \\(C = 2\\pi r\\) [1].',
      snip: 'Diện tích hình tròn được tính bằng công thức S = \\pi r^2. Chu vi hình tròn là C = 2\\pi r.'
    },
    {
      re: /lý|vận tốc|ohm|điện trở|cường độ/i,
      page: 112,
      ans: 'Định luật Ohm phát biểu rằng cường độ dòng điện chạy qua dây dẫn tỉ lệ thuận với hiệu điện thế đặt vào hai đầu dây và tỉ lệ nghịch với điện trở của dây [1].\n\n| Đại lượng | Ký hiệu | Đơn vị |\n|---|---|---|\n| Hiệu điện thế | U | Vôn (V) |\n| Cường độ dòng điện | I | Ampe (A) |\n| Điện trở | R | Ôm (Ω) |',
      snip: 'Công thức định luật Ohm: I = U / R. Trong đó U là hiệu điện thế, I là cường độ dòng điện, R là điện trở.'
    },
    {
      re: /văn|chí phèo|nam cao|điện biên phủ|lịch sử/i,
      page: 28,
      ans: 'Tác phẩm "Chí Phèo" của nhà văn Nam Cao kể về bi kịch của người nông dân bị tha hóa bởi xã hội phong kiến và thực dân nửa phong kiến [1]. Hình ảnh Chí Phèo là tiếng kêu cứu của con người bị tước đoạt quyền làm người.',
      snip: 'Chí Phèo là một kiệt tác của Nam Cao, phản ánh hiện thực xã hội Việt Nam trước Cách mạng tháng Tám, tố cáo xã hội đã biến người nông dân lương thiện thành quỷ dữ.'
    },
  ];

  async function chatMock({ message, sourceIds }, h) {
    const hit = DEMO_QA.find(x => x.re.test(message));
    const sid = sourceIds[0] ?? null;
    
    const source = sid == null ? 'Dữ liệu SGK có sẵn' : ((h.sourceName && h.sourceName(sid)) || 'Sách giáo khoa.pdf');
    const st = t => h.onStatus && h.onStatus(t);
    const chk = () => { if (h.signal && h.signal.aborted) throw abortErr(); };

    st('Đang tìm trong ' + (sourceIds.length ? sourceIds.length + ' tài liệu của bạn' : 'kho dữ liệu chung') + '…');
    await sleep(600); chk();
    st('Đang xếp hạng lại kết quả…'); await sleep(300); chk();
    st('Đang tổng hợp câu trả lời…'); await sleep(300); chk();

    if (hit) h.onCitations([
      { id: 1, sourceId: sid, source, page: hit.page, excerpt: hit.snip },
      { id: 2, sourceId: sid, source, page: hit.page + 1, excerpt: 'Đoạn liên quan khác được truy xuất từ tài liệu (nội dung minh họa).' }
    ]);

    if (!hit && h.onNotFound) h.onNotFound();

    const ans = hit ? hit.ans : 'Tôi không tìm thấy thông tin liên quan trong dữ liệu hiện có. Bạn có thể thử diễn đạt lại câu hỏi cụ thể hơn, hoặc tải lên tài liệu chứa thông tin bạn cần.';

    if (!hit) {
      h.onToken(ans);
    } else {
      for (const w of ans.split(' ')) {
        await sleep(40); chk(); h.onToken(w + ' ');
      }
    }
  }

  const normCites = list => (Array.isArray(list) ? list : []).map((c, i) => {
    const n = c.node || c;
    const m = n.metadata || {};
    const pg = m.page_label ?? m.page ?? c.page ?? (/\d+/.exec(c.article || '') || [])[0];
    const rawId = c.id ?? i + 1;

    return {
      id: Number.isFinite(+rawId) ? +rawId : (i + 1),
      sourceId: c.sourceId ?? c.file_id ?? m.file_id ?? null,
      source: c.source ?? c.title ?? m.file_name ?? m.title ?? 'Nguồn',
      page: pg != null && pg !== '' ? (Number(pg) || pg) : null,
      excerpt: String(c.excerpt ?? c.snippet ?? c.text ?? n.text ?? ''),
      nodeId: c.nodeId ?? null,
    };
  });

  async function readSSE(res, onEvent) {
    const reader = res.body.getReader();
    const dec = new TextDecoder('utf-8');
    let buf = '';

    const emit = block => {
      const data = block.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('');
      if (!data || data === '[DONE]') return;
      let ev;
      try { ev = JSON.parse(data); } catch (_) { ev = { event: 'token', content: data }; }
      onEvent(ev);
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const parts = buf.split('\n\n');
      buf = parts.pop();
      parts.forEach(emit);
    }
    if (buf.trim()) emit(buf);
  }

  async function chatReal({ message, sourceIds }, h) {
    let res;
    try {
      res = await fetch(CONFIG.BASE + CONFIG.EP.chat, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: h.signal,
        body: JSON.stringify({
          question: message,
          top_k: CONFIG.TOP_K,
          ...(sourceIds.length ? { file_ids: sourceIds } : {})
        })
      });
    } catch (e) {
      throw e.name === 'AbortError' ? e : fail('network', 'fetch failed');
    }

    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).detail || ''; } catch (_) {}
      throw fail('server', detail || ('HTTP ' + res.status));
    }

    let err = '';
    await readSSE(res, j => {
      if (j.event === 'status') {
        h.onStatus && h.onStatus(j.message || j.content || '');
      } else if (j.event === 'error') {
        err = j.message || 'stream error';
      } else if (j.event === 'citations') {
        h.onCitations(normCites(j.citations));
      } else if (j.event === 'no_results' || j.event === 'not_found') {
        h.onNotFound && h.onNotFound();
        if (j.message) h.onToken(j.message);
      } else if (j.event === 'token') {
        h.onToken(j.content || '');
      }
    });

    if (err) throw fail('server', err);
  }

  async function checkHealth() {
    try {
      const res = await fetch(CONFIG.BASE + CONFIG.EP.health);
      return res.ok;
    } catch (_) {
      return false;
    }
  }

  // ==========================================================================
  // PUBLIC API
  // ==========================================================================
  return {
    CONFIG, validateFile, checkHealth,
    uploadFile: (f, cb) => (CONFIG.USE_MOCK ? uploadMock : uploadReal)(f, cb),
    sendChat: (req, h) => (CONFIG.USE_MOCK ? chatMock : chatReal)(req, h),
    listFiles: () => (CONFIG.USE_MOCK ? Promise.resolve([]) : listFilesReal()),
    deleteFile: fileId => (CONFIG.USE_MOCK ? Promise.resolve() : deleteFileReal(fileId)),
    
    // API MỚI: Dùng để Polling trạng thái xử lý ngầm
    getFileStatus: (fileId) => {
      if (CONFIG.USE_MOCK) {
        // Giả lập trả về trạng thái hoàn tất ngay lập tức
        return Promise.resolve({ status: 'indexed', progress: 100, message: 'Mock sẵn sàng' });
      }
      return getFileStatusReal(fileId);
    }
  };
})();