const { CONFIG, validateFile } = api;
const $ = id => document.getElementById(id);

const state = {
  files: new Map(), messages: [], sending: false, cites: [], userToggled: false, seq: 0, mode: 'checking', srcOpen: true
};

const uid = p => p + '-' + String(++state.seq).padStart(3, '0');
const ico = n => `<svg class="i" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const allFiles = () => [...state.files.values()];
const readyFiles = () => allFiles().filter(f => f.status === 'ready');
const refOf = f => f.serverId ?? f.id;

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

async function loadExistingFiles() {
  try {
    const list = await api.listFiles();
    for (const item of list) {
      const id = uid('file');
      state.files.set(id, { id, name: item.name, size: item.size, type: 'application/pdf', status: 'ready', progress: 100, selected: true, serverId: item.file_id });
    }
    renderFiles();
  } catch (e) {}
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
  $('show-src-btn').style.display = open ? 'none' : 'inline-grid';
}

$('cites-toggle').onclick = () => setCitesOpen(!citesOpen(), true);
$('cites-close').onclick = () => setCitesOpen(false, true);
$('hide-src-btn').onclick = () => setSrcOpen(false);
$('show-src-btn').onclick = () => setSrcOpen(true);
$('scrim').onclick = () => { setCitesOpen(false, true); setSrcOpen(false); };

// ================= RENDER FILES =================
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
    if (f.status === 'ready') {
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = f.selected;
      cb.onchange = () => { f.selected = cb.checked; renderFiles(); };
      el.appendChild(cb);
    }
    const mid = document.createElement('div'); mid.className = 'mid';
    const nm = document.createElement('div'); nm.className = 'nm'; nm.title = f.name; nm.textContent = f.name;
    const ss = document.createElement('span'); ss.className = 'stt ' + f.status; ss.innerHTML = `<i class="dot"></i> ${f.status === 'uploading' ? f.progress + '%' : (f.status === 'ready' ? 'Sẵn sàng' : 'Lỗi')}`;
    mid.append(nm, ss); el.appendChild(mid);
    
    if (f.status === 'uploading' || f.status === 'processing') {
      const bar = document.createElement('div'); bar.className = 'bar busy';
      bar.innerHTML = `<i style="width:${f.progress}%"></i>`; mid.appendChild(bar);
    }

    const x = document.createElement('button'); x.className = 'icon-btn x'; x.innerHTML = ico('x');
    x.title = 'Xóa tài liệu';
    x.onclick = async (e) => { 
      e.stopPropagation();
      if (f.abort) f.abort(); 
      if (f.serverId) {
        try { await api.deleteFile(f.serverId); } 
        catch (err) { toast('Không xóa được trên máy chủ, đã ẩn khỏi giao diện.', 'warn'); }
      }
      state.files.delete(f.id); renderFiles(); 
    };
    el.appendChild(x); list.appendChild(el);
  });
  syncSend();
}

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
  const h = api.uploadFile(f.blob, { onProgress: p => { f.progress = p; renderFiles(); }, onProcessing: () => { f.status = 'processing'; renderFiles(); } });
  f.abort = h.abort;
  h.promise.then(r => { f.status = 'ready'; f.serverId = r.id; renderFiles(); toast('Tải lên thành công.'); })
   .catch(e => { if (e.name === 'AbortError') return; f.status = 'error'; renderFiles(); });
}

const scrollToBottom = () => { const c = $('chat-container'); c.scrollTop = c.scrollHeight; };

// ================= MARKDOWN & KATEX PARSER =================
function renderRichInline(parent, text, m) {
  text = text.replace(/\s*\[(\d+)\]\s*([.,:;!?])/g, '$2[$1]').replace(/\s+\[(\d+)\]/g, '[$1]');

  if (/<[a-z]+>/i.test(text)) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(`<div>${text}</div>`, 'text/html');
    const childNodes = Array.from(doc.body.firstChild.childNodes);
    
    childNodes.forEach(node => {
      if (node.nodeType === Node.TEXT_NODE) {
        processTextNode(node.textContent);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.tagName === 'SUP') {
          const sup = document.createElement('sup'); sup.textContent = node.textContent; parent.appendChild(sup);
        } else { parent.appendChild(node.cloneNode(true)); }
      }
    });
  } else {
    processTextNode(text);
  }

  function processTextNode(txt) {
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
          parent.appendChild(sup);
        } else { parent.append(p); }
      }
      else parent.append(p);
    });
  }
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
      const wrap = document.createElement('div'); wrap.className = 'tbl';
      wrap.innerHTML = `<table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
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
  
  // Hiển thị chip nguồn nếu LLM không inline cite [n], kèm tên file (đã escape HTML)
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
  
  // Phân biệt Abort (đã dừng) với lỗi thật (network/server)
  else if (m.status === 'error') {
    const isAbort = m.errKind === 'abort';
    b.innerHTML = ico(isAbort ? 'stop' : 'alert');
    const sp = document.createElement('span');
    sp.textContent = isAbort 
      ? (m.content || 'Đã dừng.') 
      : (m.errKind === 'network' ? 'Không thể kết nối máy chủ.' : (m.errMessage || 'Đã xảy ra lỗi khi xử lý.'));
    b.appendChild(sp);
    
    // Không hiện nút "Thử lại" cho Abort vì đây là hành động chủ động của user
    if (!isAbort) {
      const rb = document.createElement('button'); rb.className = 'retry'; rb.textContent = 'Thử lại'; rb.onclick = () => retry(m); b.appendChild(rb);
    }
  }
  
  else {
    b.textContent = ''; richText(b, m.content, m);
    if (m.status === 'streaming') {
      const caret = document.createElement('span'); caret.className = 'caret'; b.appendChild(caret);
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

async function run(m) {
  state.sending = true; state.active = m;
  Object.assign(m, { status: 'pending', content: '', citations: [], label: '', errKind: null, errMessage: null, notFound: false });
  renderMsg(m); syncSend(); scrollToBottom();
  
  try {
    await api.sendChat(m.request, {
      signal: (m.ctrl = new AbortController()).signal,
      sourceName: id => (allFiles().find(f => refOf(f) === id) || {}).name,
      onStatus: s => { if (m.status === 'pending') { m.label = s; renderMsg(m); } },
      onNotFound: () => { m.notFound = true; },
      // Render toàn bộ citations từ backend, không lọc bỏ khi đang stream
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
    
    // Đồng bộ lại lần cuối
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

const retry = m => { if (!state.sending) run(m); };

function submit(text) {
  if (state.sending) return;
  const q = (text ?? $('user-input').value).trim(); if (!q) return;
  const ids = readyFiles().filter(f => f.selected).map(refOf);
  $('user-input').value = ''; $('user-input').style.height = 'auto';
  const u = { id: uid('msg'), role: 'user', content: q, status: 'complete' };
  const a = { id: uid('msg'), role: 'assistant', content: '', status: 'pending', citations: [], request: { message: q, sourceIds: ids } };
  state.messages.push(u, a); renderMsg(u); renderMsg(a); run(a);
}

function stopGen() { if (state.active && state.active.ctrl) state.active.ctrl.abort(); }
$('send-btn').onclick = () => state.sending ? stopGen() : submit();
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
  if (state.sending) { if (state.active && state.active.ctrl) state.active.ctrl.abort(); state.sending = false; }
  const b = $('clear-btn');
  if (!b.classList.contains('armed')) { b.classList.add('armed'); b.innerHTML = ico('trash') + '<span>Xóa hết?</span>'; clearTimer = setTimeout(disarmClear, 3000); return; }
  
  disarmClear(); state.messages = []; 
  document.querySelectorAll('#chat-container .message-row:not([data-static])').forEach(n => n.remove());
  renderCites([]); if (!state.userToggled) setCitesOpen(false);
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

// citeCard hiển thị thêm tên file
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

  let rawText = c.excerpt || '';
  rawText = rawText.replace(/#{1,6}\s*[A-Z]$/gm, '').replace(/^---$/gm, '').trim();
  rawText = rawText.replace(/[\u{1F300}-\u{1F64F}\u{1F680}-\u{1F6FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{1F900}-\u{1F9FF}\u{1F1E6}-\u{1F1FF}]/gu, '');
  rawText = rawText.replace(/([^\n])(#{1,6}\s)/g, '$1\n\n$2');

  let headings = [];
  let bodyParts = [];

  let lines = rawText.split('\n');
  lines.forEach(line => {
    line = line.trim();
    if (!line) return;
    
    if (line.startsWith('#')) {
      let content = line.replace(/^#+\s*/, '');
      let boldMatch = content.match(/\*\*(.*?)\*\*/);
      
      if (boldMatch) {
        let h1 = content.substring(0, boldMatch.index).replace(/\*\*/g, '').trim();
        if (h1) headings.push(h1);
        headings.push(boldMatch[1].trim());
        
        let rest = content.substring(boldMatch.index + boldMatch[0].length).trim();
        if (rest) bodyParts.push(rest);
      } else {
        if (content.length > 80) {
          let breakIndex = content.indexOf('. ', 40);
          if (breakIndex === -1) breakIndex = content.indexOf(': ', 40);
          
          if (breakIndex !== -1) {
            headings.push(content.substring(0, breakIndex + 1).trim());
            bodyParts.push(content.substring(breakIndex + 1).trim());
          } else {
            headings.push(content);
          }
        } else {
          headings.push(content);
        }
      }
    } else {
      bodyParts.push(line);
    }
  });

  let bodyText = bodyParts.join('\n\n').trim();
  if (bodyText && !/[.!?;:]$/.test(bodyText)) bodyText += '...';

  if (headings.length > 0) {
    const hDiv = document.createElement('div');
    hDiv.className = 'cite-heading';
    hDiv.textContent = headings.join(' - ');
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
    box.innerHTML = `<div class="empty">${ico('book')}<b>Chưa có trích dẫn</b>Các đoạn văn bản liên quan sẽ xuất hiện ở đây.</div>`;
    return;
  }
  list.forEach(c => box.appendChild(citeCard(c)));
}

renderCites([]); renderFiles(); setCitesOpen(false);