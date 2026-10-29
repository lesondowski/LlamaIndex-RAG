const { CONFIG, validateFile } = api;
const $ = id => document.getElementById(id);

const state = {
  files: new Map(), messages: [], sending: false, cites: [], userToggled: false, seq: 0, mode: 'checking', srcOpen: true,
  // Bộ đếm lần tải danh sách tài liệu: lần tải mới hơn thì bỏ kết quả của lần cũ.
  filesSeq: 0
};

const uid = p => p + '-' + String(++state.seq).padStart(3, '0');
const ico = n => `<svg class="i" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const allFiles = () => [...state.files.values()];
const readyFiles = () => allFiles().filter(f => f.status === 'ready');
const refOf = f => f.serverId ?? f.id;
const selectedIds = () => readyFiles().filter(f => f.selected).map(refOf);

function toast(msg, type = 'ok', ttl = 4500, title) {
  const el = document.createElement('div'); el.className = 'toast ' + (type === 'ok' ? '' : type);
  if (title) { const b = document.createElement('b'); b.textContent = title; b.style.display = 'block'; el.appendChild(b); }
  el.append(msg); $('toasts').appendChild(el); setTimeout(() => el.remove(), ttl);
}

function setStatus(mode) {
  state.mode = mode;
  $('status').hidden = mode === 'ok' || mode === 'demo';
  $('status').className = 'status-badge ' + mode;
  $('status-text').textContent = { ok: 'Đã kết nối', demo: 'Demo', offline: 'Mất kết nối', checking: 'Đang kết nối...' }[mode];
}
setStatus(CONFIG.USE_MOCK ? 'demo' : 'checking');
if (!CONFIG.USE_MOCK) {
  const chk = async () => setStatus(await api.checkHealth() ? 'ok' : 'offline');
  chk(); setInterval(chk, 20000); loadExistingFiles();
}

// ================= LOAD & RENDER FILES =================
async function loadExistingFiles() {
  const seq = ++state.filesSeq;
  let list = [];
  try {
    list = await api.listFiles();
  } catch (e) {
    console.error('[App] load existing files error', e);
    return;
  }
  if (seq !== state.filesSeq) return;   // đã có lần tải mới hơn → bỏ kết quả này

  for (const item of list) {
    const id = uid('file');
    
    // Map trạng thái từ Backend sang Frontend
    let mappedStatus = 'saved';
    if (item.status === 'indexed') mappedStatus = 'ready';
    else if (item.status === 'processing') mappedStatus = 'processing';

    const fileObj = {
      id,
      name: item.name,
      size: item.size,
      type: 'application/pdf',
      status: mappedStatus,
      progress: mappedStatus === 'ready' ? 100 : 50, // Nếu đang processing, cho thanh chạy 50%
      selected: mappedStatus === 'ready' || mappedStatus === 'saved', 
      blob: null,
      serverId: item.file_id,
      message: mappedStatus === 'processing' ? 'Đang xử lý ngầm...' : ''
    };
    
    state.files.set(id, fileObj);

    // Nếu phát hiện file đang xử lý dở (VD: user F5 trang), tự động gọi Polling tiếp
    if (mappedStatus === 'processing') {
      pollFileStatus(fileObj, item.file_id);
    }
  }
  renderFiles();
}

// Map status -> function sinh label hiển thị
const STATUS_LABEL = {
  uploading: f => 'Đang tải lên... ' + f.progress + '%',
  processing: f => {
    // Dựa vào % tiến trình để đưa ra thông điệp thân thiện
    if (f.progress < 20) return 'Đang đọc tài liệu...';
    if (f.progress < 80) return 'Đang xử lý nội dung...';
    return 'Sắp hoàn tất...';
  },
  ready: () => 'Sẵn sàng',
  saved: () => 'Đã lưu',
  error: () => 'Lỗi xử lý file'
};

function renderFiles() {
  const list = $('src-list'); list.textContent = '';
  const rd = readyFiles(); const sel = rd.filter(f => f.selected).length;
  
  $('src-count').textContent = allFiles().length;
  $('select-all-row').hidden = !rd.length;
  $('select-all').checked = rd.length > 0 && sel === rd.length;
  $('sel-sum').textContent = sel + '/' + rd.length + ' tài liệu được chọn';
  $('bulk-del-btn').hidden = sel === 0;
  $('bulk-del-label').textContent = 'Xóa (' + sel + ')';
  
  allFiles().forEach(f => {
    const el = document.createElement('div'); el.className = 'src ' + f.status;
    
    if (f.status === 'ready' || f.status === 'saved') {
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = f.selected;
      cb.onchange = () => { f.selected = cb.checked; renderFiles(); };
      el.appendChild(cb);
    }
    
    const mid = document.createElement('div'); mid.className = 'mid';
    const nm = document.createElement('div'); nm.className = 'nm'; nm.title = f.name; nm.textContent = f.name;
    
    const ss = document.createElement('span'); 
    ss.className = 'stt ' + f.status; 
    const label = STATUS_LABEL[f.status] ? STATUS_LABEL[f.status](f) : f.status;
    ss.innerHTML = `<i class="dot"></i> ${label}`;
    
    mid.append(nm, ss); el.appendChild(mid);
    
    // Thanh tiến trình (hiển thị khi upload hoặc processing)
        if (f.status === 'uploading' || f.status === 'processing') {
      const bar = document.createElement('div'); bar.className = 'bar busy';
      if (f.status === 'uploading') {
        // Đang tải file lên: chạy đúng % thực tế
        bar.innerHTML = `<i style="width:${f.progress}%"></i>`; 
      } else {
        // Đang xử lý ngầm: chạy hiệu ứng vô định
        bar.innerHTML = `<i class="indeterminate"></i>`; 
      }
      mid.appendChild(bar);
    }

    const x = document.createElement('button'); x.className = 'icon-btn x'; x.innerHTML = ico('x');
    x.title = 'Xóa tài liệu';
    x.onclick = async (e) => { 
      e.stopPropagation();
      if (f.abort) f.abort(); 
      if (f.serverId) {
        try { 
          await api.deleteFile(f.serverId); 
          toast('Đã xóa tài liệu.');
        } 
        catch (err) { 
          console.error('Delete failed:', err);
          toast('Không xóa được: ' + err.message, 'err'); 
          return; 
        }
      }
      state.files.delete(f.id); renderFiles(); 
    };
    el.appendChild(x); list.appendChild(el);
  });
  syncSend();
}

function setCitesOpen(open, manual) {
  if (manual) state.userToggled = true;
  $('layout').classList.toggle('cites-open', open);
  $('cites-panel').inert = !open;
  $('cites-toggle').setAttribute('aria-expanded', String(open));
}
const citesOpen = () => $('layout').classList.contains('cites-open');

function setSrcOpen(open) {
  state.srcOpen = open;
  $('layout').classList.toggle('src-closed', !open);
  // ≤860px cột trái là ngăn kéo phủ lên khung chat, CSS mở nó bằng class src-open.
  $('layout').classList.toggle('src-open', open);
  $('show-src-btn').style.display = open ? 'none' : 'inline-grid';
}
// Đổi cỡ màn hình qua mốc 860px (thu nhỏ cửa sổ, xoay tablet): màn hẹp thì đóng ngăn kéo để
// hiện nút mở, màn rộng thì mở lại thanh bên — không để thanh bên biến mất mà không có nút mở.
const narrowScreen = matchMedia('(max-width: 860px)');
narrowScreen.addEventListener('change', e => setSrcOpen(!e.matches));

$('cites-toggle').onclick = () => setCitesOpen(!citesOpen(), true);
$('cites-close').onclick = () => setCitesOpen(false, true);
$('hide-src-btn').onclick = () => setSrcOpen(false);
$('show-src-btn').onclick = () => setSrcOpen(true);
$('scrim').onclick = () => { setCitesOpen(false, true); setSrcOpen(false); };

// ================= KEYBOARD SHORTCUTS =================
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault(); setCitesOpen(!citesOpen(), true); return;
  }
  if (e.key === 'Escape') {
    if (state.sending) { stopGen(); return; }
    if (innerWidth <= 1200 && citesOpen()) setCitesOpen(false, true);
    setSrcOpen(false);
  }
});

// ================= UPLOAD EVENTS =================
const fi = $('file-input'); const dz = $('dropzone');
dz.onclick = () => fi.click();
fi.onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };
['dragenter', 'dragover'].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.add('drag'); }));
dz.addEventListener('dragleave', e => { if (!dz.contains(e.relatedTarget)) dz.classList.remove('drag'); });
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); addFiles([...e.dataTransfer.files]); });

async function addFiles(list) {
  for (const blob of list) {
    if (allFiles().some(f => f.name === blob.name && f.size === blob.size)) { toast('File đã tồn tại.', 'warn'); continue; }
    const err = await validateFile(blob); if (err) { toast(err.message, 'err'); continue; }
    const id = uid('file'); state.files.set(id, { id, name: blob.name, size: blob.size, status: 'uploading', progress: 0, selected: true, blob, serverId: null });
    startUpload(id);
  }
}

function startUpload(id) {
  const f = state.files.get(id); if (!f) return;
  Object.assign(f, { status: 'uploading', progress: 0 }); renderFiles();
  
  const h = api.uploadFile(f.blob, { 
    onProgress: p => { f.progress = p; renderFiles(); }, 
    onProcessing: () => { f.status = 'processing'; f.message = 'Đang chờ xử lý...'; renderFiles(); } 
  });
  
  f.abort = h.abort;
  
  h.promise.then(r => { 
    // SỬA LỖI: api.js mới trả về r.file_id thay vì r.id
    f.serverId = r.file_id; 
    f.status = 'processing'; 
    f.message = r.message || 'Đang xử lý ngầm...';
    renderFiles(); 
    
    // Bắt đầu Polling liên tục để cập nhật trạng thái từ Background Task
    pollFileStatus(f, r.file_id);
  })
   .catch(e => {
     if (e.name === 'AbortError') return;
     f.status = 'error'; 
     f.message = e.message || 'Lỗi upload';
     renderFiles(); 
   });
}

// Hàm Polling: Hỏi thăm Backend mỗi 1.5 giây
async function pollFileStatus(f, serverId) {
  let attempts = 0;
  const maxAttempts = 80; // ~2 phút timeout (đề phòng file lớn parse lâu)

  const check = async () => {
    // Dừng polling nếu file đã xong hoặc bị lỗi, hoặc bị xóa khỏi UI
    if (f.status === 'ready' || f.status === 'error' || !state.files.has(f.id)) return;

    try {
      const res = await api.getFileStatus(serverId);
      
      f.message = res.message || 'Đang xử lý...';
      
      if (res.status === 'indexed') {
        f.status = 'ready';
        f.progress = 100;
        f.selected = true; // Tự động chọn khi xong
        renderFiles();
        toast(`Tài liệu "${f.name}" đã sẵn sàng!`);
        return;
      }
      
      if (res.status === 'error') {
        f.status = 'error';
        renderFiles();
        toast(`Lỗi xử lý "${f.name}": ${res.message}`, 'err');
        return;
      }
      
      // Cập nhật UI nếu vẫn đang processing
      renderFiles();
      
      attempts++;
      if (attempts < maxAttempts) {
        setTimeout(check, 1500);
      } else {
        f.status = 'error'; 
        f.message = 'Quá thời gian xử lý.';
        renderFiles(); 
        toast('Quá thời gian xử lý tài liệu.', 'err');
      }
      
    } catch (e) {
      // Nếu mất mạng tạm thời, thử lại sau 3s
      setTimeout(check, 3000);
    }
  };
  
  check();
}

const scrollToBottom = () => { const c = $('chat-container'); c.scrollTop = c.scrollHeight; };

// ================= MARKDOWN & KATEX PARSER =================
// [AI] Chữ đến từ LLM và từ PDF người dùng tải lên → chỉ giữ vài thẻ định dạng an toàn, bỏ mọi
// thuộc tính; thẻ lạ (<img onerror>, <script>, <iframe>...) bị bỏ, chỉ giữ chữ bên trong.
const SAFE_TAGS = new Set(['SUP', 'SUB', 'B', 'STRONG', 'I', 'EM', 'U', 'BR']);
const HAS_SAFE_TAG = /<\/?(sup|sub|b|strong|i|em|u|br)\b[^>]*>/i;

// [AI] Chuẩn hóa dấu trích dẫn: "[3, 4, 5]", "[1], [2]", "[1] [2]" → "[3][4][5]" (một cụm liền) và
// bỏ khoảng trắng trước cụm để số dính vào chữ. KHÔNG đẩy dấu câu lên trước số trích dẫn: trước đây
// "đời sống [4]. Đạo hàm" bị đổi thành "đời sống. [4] Đạo hàm" → dấu chấm và số lệch sang câu sau.
function normalizeCites(text) {
  return text
    .replace(/\[(\d+(?:\s*,\s*\d+)+)\]/g, (_, ns) => ns.split(/\s*,\s*/).map(n => `[${n}]`).join(''))
    .replace(/(\[\d+\])(?:\s*,\s*|\s+)(?=\[\d+\])/g, '$1')
    .replace(/[ \t]+(?=\[\d+\])/g, '');
}

function renderRichInline(parent, text, m) {
  if (m.citations.length) text = normalizeCites(text);
  if (!HAS_SAFE_TAG.test(text)) { renderInline(parent, text, m); return; }

  const doc = new DOMParser().parseFromString(`<div>${text}</div>`, 'text/html');
  const walk = (src, dst) => src.childNodes.forEach(node => {
    if (node.nodeType === Node.TEXT_NODE) renderInline(dst, node.textContent, m);
    else if (node.nodeType === Node.ELEMENT_NODE) {
      if (!SAFE_TAGS.has(node.tagName)) { walk(node, dst); return; }
      const el = document.createElement(node.tagName); walk(node, el); dst.appendChild(el);
    }
  });
  walk(doc.body.firstChild, parent);
}

// [AI] Gói từ đứng ngay trước (hoặc công thức / chữ đậm ngắn) cùng các số trích dẫn vào một khung
// không xuống dòng → số trích dẫn không bị rớt một mình sang dòng mới.
function citeGlue(parent) {
  const last = parent.lastChild;
  if (last && last.nodeType === Node.ELEMENT_NODE && last.classList.contains('cite-glue')) return last;
  const glue = document.createElement('span'); glue.className = 'cite-glue';
  if (last && last.nodeType === Node.TEXT_NODE) {
    const w = /\S+$/.exec(last.textContent);
    if (w) { last.textContent = last.textContent.slice(0, w.index); glue.append(w[0]); }
  } else if (last && last.nodeType === Node.ELEMENT_NODE && last.textContent.length <= 30) {
    glue.appendChild(last);
  }
  parent.appendChild(glue);
  return glue;
}

function renderInline(parent, txt, m) {
    const parts = txt.split(/(\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\*\*[^*]+\*\*|`[^`\n]+`|\*(?!\s)[^*\n]+?(?<!\s)\*|\[\d+\])/);
    parts.forEach(p => {
      if (!p) return; let mm;
      if ((mm = /^\$\$([\s\S]*?)\$\$$/.exec(p))) {
        const span = document.createElement('span');
        try { katex.render(mm[1], span, { displayMode: true, throwOnError: false }); parent.appendChild(span); } catch (e) { parent.append(p); }
      }
      else if ((mm = /^\$([^$\n]+)\$$/.exec(p))) {
        const span = document.createElement('span');
        try { katex.render(mm[1], span, { displayMode: false, throwOnError: false }); parent.appendChild(span); } catch (e) { parent.append(p); }
      }
      else if ((mm = /^\*\*([^*]+)\*\*$/.exec(p))) {
        const st = document.createElement('strong'); renderRichInline(st, mm[1], m); parent.appendChild(st);
      }
      else if ((mm = /^`([^`]+)`$/.exec(p))) {
        const cd = document.createElement('code'); cd.textContent = mm[1]; parent.appendChild(cd);
      }
      else if ((mm = /^\*([^*]+)\*$/.exec(p))) {
        const em = document.createElement('em'); renderRichInline(em, mm[1], m); parent.appendChild(em);
      }
      else if ((mm = /^\[(\d+)\]$/.exec(p))) {
        const c = m.citations.find(x => x.id === +mm[1]);
        if (c) {
          const sup = document.createElement('sup');
          sup.className = 'cite-mark'; sup.textContent = `[${c.id}]`; sup.title = `Xem trích dẫn ${c.id}`;
          sup.onclick = (e) => { e.preventDefault(); showCite(m.citations, c.id); };
          citeGlue(parent).appendChild(sup);
        } else { parent.append(p); }
      }
      else parent.append(p);
    });
}

const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
const LI = /^\s*([-*]|\d+\.)\s+(.*)$/;

function richText(b, text, m) {
  const lines = text.split('\n'); let i = 0; let para = [];
  const flush = () => { if (!para.length) return; const p = document.createElement('p'); renderRichInline(p, para.join('\n'), m); b.appendChild(p); para = []; };

  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*\|/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush(); const head = cells(ln); const rows = []; i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(cells(lines[i++])); }
      // [AI] Dựng bảng bằng DOM thay vì innerHTML: ô bảng lấy từ PDF/LLM có thể chứa "<" hoặc thẻ lạ,
      // và mỗi ô vẫn hiển thị được công thức $...$, chữ đậm, số trích dẫn.
      const row = (tag, cs) => {
        const tr = document.createElement('tr');
        cs.forEach(c => { const td = document.createElement(tag); renderRichInline(td, c, m); tr.appendChild(td); });
        return tr;
      };
      const table = document.createElement('table');
      const thead = document.createElement('thead'); thead.appendChild(row('th', head));
      const tbody = document.createElement('tbody'); rows.forEach(r => tbody.appendChild(row('td', r)));
      table.append(thead, tbody);
      const wrap = document.createElement('div'); wrap.className = 'tbl'; wrap.appendChild(table);
      b.appendChild(wrap); continue;
    }
    const li = LI.exec(ln);
    if (li) {
      flush(); const ord = /\d/.test(li[1]); const list = document.createElement(ord ? 'ol' : 'ul');
      while (i < lines.length) {
        if (!lines[i].trim()) { let next = i + 1; while (next < lines.length && !lines[next].trim()) next++; if (next < lines.length && LI.test(lines[next])) { i++; continue; } }
        const x = LI.exec(lines[i]); if (!x || /\d/.test(x[1]) !== ord) break;
        const it = document.createElement('li'); renderRichInline(it, x[2], m); list.appendChild(it); i++;
      }
      b.appendChild(list); continue;
    }
    if (/^#{1,4}\s/.test(ln)) {
      flush();
      const level = ln.match(/^#+/)[0].length;
      const h = document.createElement('h' + Math.min(level + 1, 4));
      renderRichInline(h, ln.replace(/^#+\s+/, ''), m);
      b.appendChild(h); i++; continue;
    }
    if (!ln.trim()) { flush(); i++; continue; }
    para.push(ln); i++;
  }
  flush();
}

// ================= MESSAGE RENDERING =================
function buildAfter(m) {
  if (m.notFound) return;
  const af = document.createElement('div'); af.className = 'after';
  
  if (m.role === 'user') {
    const ed = document.createElement('button'); ed.className = 'icon-btn edit-btn'; ed.title = 'Sửa câu hỏi';
    ed.innerHTML = ico('edit'); ed.onclick = () => editUserMessage(m);
    af.appendChild(ed); m.ct.appendChild(af); return;
  }

  const cp = document.createElement('button'); cp.className = 'icon-btn'; cp.title = 'Sao chép';
  cp.innerHTML = ico('copy'); cp.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(m.content); cp.innerHTML = ico('check'); setTimeout(() => cp.innerHTML = ico('copy'), 1400); };
  
  const rg = document.createElement('button'); rg.className = 'icon-btn'; rg.title = 'Tạo lại';
  rg.innerHTML = ico('refresh'); rg.onclick = () => retry(m);
  
  const tu = document.createElement('button'); tu.className = 'icon-btn'; tu.title = 'Tốt'; tu.innerHTML = ico('thumbs-up'); tu.onclick = () => toast('Cảm ơn phản hồi!');
  const td = document.createElement('button'); td.className = 'icon-btn'; td.title = 'Chưa tốt'; td.innerHTML = ico('thumbs-down'); td.onclick = () => toast('Cảm ơn phản hồi!');

  af.append(cp, rg, tu, td);
  
  if (m.citations.length && !/\[\d+\]/.test(m.content)) {
    const sr = document.createElement('div'); sr.className = 'sources';
    m.citations.forEach(c => {
      const k = document.createElement('button'); k.className = 'chip';
      const safeName = String(c.source || 'Nguồn').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      k.innerHTML = `<b>${c.id}</b> <span class="chip-src">${safeName}</span> · Tr.${c.page}`; 
      k.onclick = () => showCite(m.citations, c.id); sr.appendChild(k);
    });
    af.appendChild(sr);
  }
  m.ct.appendChild(af);
}

function renderMsg(m) {
  if (!m.el) {
    const row = document.createElement('div'); row.className = 'message-row ' + m.role;
    const ct = document.createElement('div'); ct.className = 'message-content';
    m.bubble = document.createElement('div'); 
    m.bubble.className = m.role === 'user' ? 'bubble user-bubble' : 'bubble ai-bubble'; 
    m.ct = ct;
    ct.appendChild(m.bubble); row.appendChild(ct); $('chat-container').appendChild(row); m.el = row;
  }
  const b = m.bubble;
  m.ct.querySelector('.after')?.remove();
  
  if (m.role === 'user') { b.textContent = m.content; buildAfter(m); return; }
  
  if (m.status === 'pending') { b.innerHTML = '<span class="dots"><i></i><i></i><i></i></span><span class="pl"></span>'; b.querySelector('.pl').textContent = m.label || 'Đang tìm câu trả lời…'; }
  else if (m.status === 'error') {
    const isAbort = m.errKind === 'abort';
    b.innerHTML = ico(isAbort ? 'stop' : 'alert');
    const sp = document.createElement('span');
    sp.textContent = isAbort 
      ? (m.content || 'Đã dừng.') 
      : (m.errKind === 'network' ? 'Không thể kết nối máy chủ.' : (m.errMessage || 'Đã xảy ra lỗi khi xử lý.'));
    b.appendChild(sp);
    
    if (!isAbort) {
      const rb = document.createElement('button'); rb.className = 'retry'; rb.textContent = 'Thử lại'; rb.onclick = () => retry(m); b.appendChild(rb);
    }
  }
  else {
    b.textContent = ''; richText(b, m.content, m);
    if (m.status === 'streaming') {
      const caret = document.createElement('span'); caret.className = 'caret';
      // Đặt con trỏ ngay cuối đoạn đang viết, không để nó rơi xuống một dòng riêng.
      let host = b.lastElementChild;
      if (host && /^(UL|OL)$/.test(host.tagName)) host = host.lastElementChild;
      (host && /^(P|LI|H[2-4])$/.test(host.tagName) ? host : b).appendChild(caret);
    } else { buildAfter(m); }
  }
}

function editUserMessage(m) {
  if (state.sending) return toast('Đợi phản hồi xong đã.', 'warn');
  const idx = state.messages.indexOf(m);
  if (idx === -1) return;
  
  $('user-input').value = m.content;
  $('user-input').dispatchEvent(new Event('input')); 
  $('user-input').focus();
  
  const toRemove = state.messages.splice(idx);
  toRemove.forEach(msg => { if (msg.el) msg.el.remove(); });

  renderCites([]); if (!state.userToggled) setCitesOpen(false);
}

// [AI] Bộ nhớ hội thoại: các tin đã xong TRƯỚC câu hỏi của m (3 lượt hỏi–đáp gần nhất), gửi kèm
// để server viết lại câu hỏi nối tiếp như "Còn chu vi của nó?" thành câu tự đủ nghĩa rồi mới tìm.
// Tính theo vị trí của m nên "Tạo lại" một câu trả lời cũ chỉ lấy các lượt TRƯỚC nó.
// Không lưu lịch sử: xóa khung chat (nút 🗑) hoặc tải lại trang là bắt đầu lại, không còn ngữ cảnh.
const HISTORY_MESSAGES = 6;
function historyBefore(m) {
  const idx = state.messages.indexOf(m);   // m là câu trả lời, ngay trước nó là câu hỏi
  return state.messages.slice(0, Math.max(idx - 1, 0))
    .filter(x => x.status === 'complete' && x.content.trim())
    .slice(-HISTORY_MESSAGES)
    .map(x => ({ role: x.role, content: x.content }));
}

async function run(m) {
  state.sending = true; state.active = m;
  Object.assign(m, { status: 'pending', content: '', citations: [], label: '', errKind: null, errMessage: null, notFound: false });
  renderMsg(m); syncSend(); scrollToBottom();

  try {
    await api.sendChat({ ...m.request, history: historyBefore(m) }, {
      signal: (m.ctrl = new AbortController()).signal,
      sourceName: id => (allFiles().find(f => refOf(f) === id) || {}).name,
      onStatus: s => { if (m.status === 'pending') { m.label = s; renderMsg(m); } },
      onNotFound: () => { m.notFound = true; },
      onCitations: cs => { 
        m.citations = cs; 
        renderCites(cs); 
        if (cs.length && !state.userToggled && innerWidth > 1200) setCitesOpen(true); 
      },
      onToken: t => { m.status = 'streaming'; m.content += t; renderMsg(m); scrollToBottom(); },
    });
    
    m.status = 'complete';
    if (!m.content.trim()) { 
      m.notFound = true; 
      m.content = m.request.sourceIds.length ? 'Tôi không tìm thấy thông tin trong tài liệu đã chọn.' : 'Tôi không tìm thấy thông tin trong dữ liệu.'; 
    }
    m.content = m.content.trim();
    
    renderCites(m.citations);
    
    if (!CONFIG.USE_MOCK) setStatus('ok');
  } catch (e) {
    if (e.name === 'AbortError') { 
      const had = m.content.trim(); m.status = 'complete'; 
      if (!had) { m.status = 'error'; m.errKind = 'abort'; m.content = 'Đã dừng.'; }
      else { m.content = had + ' [Đã dừng]'; }
    }
    else {
      m.status = 'error';
      m.errKind = e.kind;
      m.errMessage = e.message; 
    }
  } finally {
    state.sending = false; renderMsg(m); syncSend(); scrollToBottom(); $('user-input').focus();
  }
}

const retry = m => {
  if (state.sending) return;
  run(m);
};

function submit(text) {
  if (state.sending) return;
  const q = (text ?? $('user-input').value).trim(); if (!q) return;
  const ids = selectedIds();
  $('user-input').value = ''; $('user-input').style.height = 'auto';
  const u = { id: uid('msg'), role: 'user', content: q, status: 'complete' };
  const a = { id: uid('msg'), role: 'assistant', content: '', status: 'pending', citations: [], request: { message: q, sourceIds: ids } };
  state.messages.push(u, a); renderMsg(u); renderMsg(a); run(a);
}

function stopGen() { if (state.active && state.active.ctrl) state.active.ctrl.abort(); }
$('send-btn').onclick = () => state.sending ? stopGen() : submit();
// Gợi ý câu hỏi ở màn chào: bấm là gửi luôn.
document.querySelectorAll('.suggestion').forEach(btn => { btn.onclick = () => submit(btn.dataset.q); });
$('user-input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
$('user-input').addEventListener('input', e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px'; syncSend(); });
const syncSend = () => { const b = $('send-btn'); b.classList.toggle('stop', state.sending); b.disabled = !state.sending && !$('user-input').value.trim(); b.innerHTML = ico(state.sending ? 'stop' : 'arrow-up'); };

// ================= DELETE LOGIC =================
let bulkDelTimer = null;
const disarmBulkDelete = () => { 
  clearTimeout(bulkDelTimer); const b = $('bulk-del-btn'); 
  if (b && b.classList.contains('armed')) { b.classList.remove('armed'); const sel = readyFiles().filter(f => f.selected).length; $('bulk-del-label').textContent = 'Xóa (' + sel + ')'; } 
};

$('bulk-del-btn').onclick = async () => {
  const b = $('bulk-del-btn'); const targets = readyFiles().filter(f => f.selected); 
  if (!targets.length) return;
  
  if (!b.classList.contains('armed')) { b.classList.add('armed'); $('bulk-del-label').textContent = 'Xóa thật?'; bulkDelTimer = setTimeout(disarmBulkDelete, 3000); return; }
  
  clearTimeout(bulkDelTimer); b.disabled = true; 
  for (const f of targets) {
    if (f.abort) f.abort(); 
    if (f.serverId) { try { await api.deleteFile(f.serverId); } catch (e) { console.error(e); } }
    state.files.delete(f.id); 
  }
  b.disabled = false; renderFiles(); toast(`Đã xóa ${targets.length} tài liệu.`, 'ok');
};

$('select-all').onchange = e => { readyFiles().forEach(f => { f.selected = e.target.checked; }); renderFiles(); };

let clearTimer = null;
const disarmClear = () => { clearTimeout(clearTimer); const b = $('clear-btn'); b.classList.remove('armed'); b.innerHTML = ico('trash'); };

$('clear-btn').onclick = () => {
  const b = $('clear-btn');
  if (!b.classList.contains('armed')) { b.classList.add('armed'); b.innerHTML = ico('trash') + '<span>Xóa hết?</span>'; clearTimer = setTimeout(disarmClear, 3000); return; }

  disarmClear(); stopGen(); state.messages = [];
  document.querySelectorAll('#chat-container .message-row:not([data-static])').forEach(n => n.remove());
  renderCites([]); if (!state.userToggled) setCitesOpen(false);
  $('user-input').focus();
};

// ================= CITATIONS LOGIC =================
function showCite(list, n) {
  if (state.cites !== list) renderCites(list);
  setCitesOpen(true);
  requestAnimationFrame(() => {
    const el = $('cite-' + n); if (!el) return;
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  });
}

// [AI] Làm sạch đoạn trích PDF trước khi hiển thị.
const EMOJI = /[\u{1F300}-\u{1F64F}\u{1F680}-\u{1F6FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{1F900}-\u{1F9FF}\u{1F1E6}-\u{1F1FF}]/gu;
function cleanExcerpt(raw) {
  let t = String(raw || '');
  // Dữ liệu cũ (gộp thành một dòng): tách tiêu đề markdown ra dòng riêng.
  // [^\n#]: không tách nhầm "##" thành "#" + "# " (sinh tiêu đề rỗng)
  if (!t.includes('\n')) t = t.replace(/([^\n#])(#{1,6}\s)/g, '$1\n\n$2');
  return t
    .replace(/<!--[\s\S]*?-->/g, '')                  // chú thích HTML (đánh dấu trang của LlamaParse)
    .replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, '') // khối mã / CSS lẫn trong PDF
    .replace(/<\/?(?!(?:sup|sub|b|strong|i|em|u|br)\b)[a-z][a-z0-9]*\b(?:\s[^<>]*)?\/?>/gi, '')  // thẻ HTML lạ (giữ chữ)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')             // ảnh markdown
    .replace(/^(#{1,6}\s+)[A-ZĐ]\s+(?=\*\*)/gm, '$1') // chữ cái mục lục thuật ngữ: "## G **Giá trị cực đại**"
    .replace(/^[ \t]*(?:>[ \t]?)+/gm, '')             // dấu trích dẫn khối "> "
    .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '')  // đường kẻ ngang (dòng kẻ của bảng bắt đầu bằng "|" nên không bị đụng)
    .replace(/#{1,6}\s*[A-Z]$/gm, '')                 // tiêu đề rác chỉ một chữ cái ("## B")
    .replace(EMOJI, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// Tiêu đề ở ĐẦU đoạn trích → dòng tiêu đề của thẻ; tiêu đề nằm giữa → chữ đậm (thẻ nhỏ, không cần h2/h3).
// LlamaParse đôi khi đánh dấu cả một đoạn văn dài là tiêu đề → dài quá thì coi là đoạn văn thường.
const HEADING_MAX = 100;
function splitExcerpt(text) {
  const lines = text.split('\n'), headings = [];
  const strip = l => l.trim().replace(/^#+\s*/, '');
  while (lines.length && (!lines[0].trim() || /^#{1,6}\s/.test(lines[0].trim()))) {
    const h = strip(lines[0]);
    if (!h) { lines.shift(); continue; }
    // Dữ liệu cũ (một dòng): "## **TIÊU ĐỀ** nội dung..." → tách phần in đậm đầu làm tiêu đề
    const bold = /^\*\*(.+?)\*\*\s+(.{20,})$/.exec(h);
    if (bold) { lines[0] = bold[2]; headings.push(bold[1].trim()); break; }
    if (h.replace(/\*\*/g, '').length > HEADING_MAX) { lines[0] = h; break; }
    headings.push(h.replace(/\*\*/g, '').trim()); lines.shift();
  }
  const body = lines.map(l => {
    if (!/^\s*#{1,6}\s/.test(l)) return l;
    const h = strip(l).replace(/\*\*/g, '').trim();
    return h.length > HEADING_MAX ? h : `**${h}**`;
  }).join('\n').trim();
  return { headings, body };
}

function citeCard(c) {
  const el = document.createElement('div'); el.className = 'cite'; el.id = 'cite-' + c.id;
  
  const top = document.createElement('div'); top.className = 'top';
  const nSpan = document.createElement('span'); nSpan.className = 'n'; nSpan.textContent = c.id;
  const fnameSpan = document.createElement('span'); fnameSpan.className = 'fname'; fnameSpan.textContent = c.source || 'Nguồn không xác định'; fnameSpan.title = c.source || 'Nguồn không xác định';
  const pgSpan = document.createElement('span'); pgSpan.className = 'pg'; pgSpan.textContent = 'Trang ' + (c.page ?? '?');
  
  const pdfBtn = document.createElement('button'); pdfBtn.className = 'icon-btn pdf-btn'; pdfBtn.title = 'Xem tài liệu gốc';
  pdfBtn.innerHTML = ico('external-link'); pdfBtn.onclick = (e) => { e.stopPropagation(); toast('Mở PDF Viewer...', 'warn'); };
  
  top.append(nSpan, fnameSpan, pgSpan, pdfBtn);
  el.appendChild(top);

  // [AI] Đoạn trích là markdown do LlamaParse đọc từ PDF, giữ nguyên xuống dòng (xem
  // backend citations.clean_excerpt): tách tiêu đề ở đầu ra dòng riêng, phần còn lại hiển thị
  // bằng richText (bảng, danh sách, công thức) thay vì một khối chữ dính liền.
  const { headings, body } = splitExcerpt(cleanExcerpt(c.excerpt));
  let bodyText = body;
  // Đoạn trích bị cắt giữa câu → thêm "…" (trừ khi dòng cuối là hàng của bảng)
  if (bodyText && !/[.!?;:…)\]|]$/.test(bodyText)) bodyText += '…';

  if (headings.length > 0) {
    const hDiv = document.createElement('div');
    hDiv.className = 'cite-heading';
    renderRichInline(hDiv, headings.join(' · '), { citations: [] });   // [AI] tiêu đề có thể chứa công thức $...$
    el.appendChild(hDiv);
  }

  const s = document.createElement('div'); 
  s.className = 's collapsed';
  const dummyMsg = { citations: [] };
  richText(s, bodyText, dummyMsg);
  el.appendChild(s);

  const toggleBtn = document.createElement('button');
  toggleBtn.className = 'cite-toggle-btn';
  toggleBtn.innerHTML = 'Xem thêm <svg class="i" style="width:12px;height:12px;"><use href="#i-chevron"/></svg>';
  el.appendChild(toggleBtn);

  requestAnimationFrame(() => {
    if (s.scrollHeight <= s.clientHeight + 5) {
      toggleBtn.style.display = 'none';
    } else {
      s.classList.add('fade');   // [AI] chỉ làm mờ đáy khi đoạn trích thật sự bị cắt
    }
  });

  el.onclick = (e) => {
    if (e.target.closest('.pdf-btn')) return; 
    const isCollapsed = s.classList.toggle('collapsed');
    toggleBtn.innerHTML = isCollapsed ? 'Xem thêm <svg class="i" style="width:12px;height:12px;"><use href="#i-chevron"/></svg>' : 'Thu gọn <svg class="i" style="width:12px;height:12px;transform:rotate(180deg);"><use href="#i-chevron"/></svg>';
  };

  return el;
}

function renderCites(list) {
  state.cites = list; const box = $('cites'); box.textContent = '';
  $('cite-count').textContent = list.length;
  if (!list.length) {
    box.innerHTML = `<div class="empty">${ico('book')}<b>Chưa có trích dẫn</b>Các đoạn trong sách dùng để trả lời sẽ hiện ở đây, kèm số trang.</div>`;
    return;
  }
  list.forEach(c => box.appendChild(citeCard(c)));
}

// ================= GIAO DIỆN SÁNG / TỐI =================
// [AI] Phần này viết với sự hỗ trợ của Claude Code.
// Theme ban đầu đã được gán ngay trong <head> (tránh nháy màu khi tải); ở đây chỉ đổi và nhớ lựa chọn.
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('theme-btn').innerHTML = ico(theme === 'dark' ? 'sun' : 'moon');
  $('theme-btn').title = theme === 'dark' ? 'Chuyển sang nền sáng' : 'Chuyển sang nền tối';
  try { localStorage.setItem('rag.theme', theme); } catch (_) { }
}
$('theme-btn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

renderCites([]); renderFiles(); setCitesOpen(false); setSrcOpen(!narrowScreen.matches);
setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');