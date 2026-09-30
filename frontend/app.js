/* ============================================================================
 * Giao diện chatbot — UI: state + render.
 * ----------------------------------------------------------------------------
 * NGUYÊN TẮC:  File này KHÔNG có logic RAG. Mọi lời gọi backend đi qua
 *              window.api (api.js). UI chỉ:
 *                1. Quản lý state (files, messages, sending, cites)
 *                2. Render state ra DOM
 *                3. Gọi api.uploadFile / api.sendChat và nhận callbacks
 * ========================================================================== */

// Destructure từ window.api — lấy CONFIG (đọc USE_MOCK) và validateFile
const { CONFIG, validateFile } = api;

// Alias ngắn cho document.getElementById — dùng khắp file
const $ = id => document.getElementById(id);

// State toàn cục của app — object duy nhất, mọi thay đổi đi qua đây
const state = {
  files: new Map(),      // Map<clientId, FileObject> — clientId dạng 'file-001'
  messages: [],          // Mảng Message — lịch sử hội thoại
  sending: false,        // Có đang chờ assistant trả lời không?
  cites: [],             // Citations đang hiển thị ở panel phải (câu trả lời mới nhất)
  userToggled: false,    // User đã tự bấm mở/đóng cites panel chưa?
                         // → Nếu rồi, không auto-mở nữa (tôn trọng lựa chọn user)
  seq: 0,                // Bộ đếm tăng dần — sinh unique ID
  mode: 'checking',      // Trạng thái kết nối: 'checking'|'ok'|'offline'|'demo'
};

// Sinh unique ID với prefix. VD: uid('file') → 'file-001', uid('msg') → 'msg-002'
// ++state.seq: tăng TRƯỚC rồi trả về (pre-increment)
// padStart(3, '0'): '1' → '001', '12' → '012'
const uid = p => p + '-' + String(++state.seq).padStart(3, '0');

// Sinh SVG icon bằng template string. VD: ico('copy') → '<svg...><use href="#i-copy"/></svg>'
// Dùng SVG sprite trong index.html — không phụ thuộc emoji/OS
const ico = n => `<svg class="i" aria-hidden="true"><use href="#i-${n}"/></svg>`;

// Format kích thước file: bytes → "1.5 MB" hoặc "512 KB"
// Math.max(1, ...): đảm bảo không hiển thị "0 KB" cho file < 512 bytes
const fmtSize = n => n > 1048576
  ? (n / 1048576).toFixed(1) + ' MB'
  : Math.max(1, Math.round(n / 1024)) + ' KB';

// Lấy tất cả file dưới dạng mảng
const allFiles = () => [...state.files.values()];

// Chỉ lấy file đã ready (upload xong + backend xử lý xong)
const readyFiles = () => allFiles().filter(f => f.status === 'ready');

// Có file nào đang upload/processing không? → Dùng để disable composer
const isBusy = () => allFiles().some(f => f.status === 'uploading' || f.status === 'processing');

// Lấy ID tham chiếu gửi lên backend:
// - Ưu tiên serverId (ID backend trả về sau upload)
// - Fallback f.id (client ID) — dùng khi mock
// ?? = nullish coalescing: chỉ fallback khi null/undefined
const refOf = f => f.serverId ?? f.id;

/* ----------------------------------------------------------------------------
 * toast — Hiển thị thông báo nổi ở góc dưới phải
 * ----------------------------------------------------------------------------
 * @param msg    — nội dung
 * @param type   — 'ok' (mặc định) | 'warn' | 'err'
 * @param ttl    — thời gian hiển thị (ms), mặc định 4500
 * @param title  — tiêu đề đậm (tùy chọn)
 * -------------------------------------------------------------------------- */
function toast(msg, type = 'ok', ttl = 4500, title) {
  // Tạo div với class 'toast' + class type (trừ 'ok' — vì 'ok' là mặc định, không cần class)
  const el = document.createElement('div');
  el.className = 'toast ' + (type === 'ok' ? '' : type);

  // role='status' → screen reader đọc thông báo
  el.setAttribute('role', 'status');

  // Nếu có title → thêm thẻ <b> ở đầu
  if (title) {
    const b = document.createElement('b');
    b.textContent = title;
    b.style.display = 'block';   // Xuống dòng
    el.appendChild(b);
  }

  el.append(msg);
  $('toasts').appendChild(el);

  // Tự động xóa sau ttl ms
  setTimeout(() => el.remove(), ttl);
}

/* ----------------------------------------------------------------------------
 * TRẠNG THÁI KẾT NỐI — Badge hiển thị "Đã kết nối / Demo / Offline"
 * -------------------------------------------------------------------------- */
const STATUS_TEXT = {
  ok: 'Đã kết nối máy chủ',
  demo: 'Chế độ demo',
  offline: 'Không thể kết nối máy chủ',
  checking: 'Đang kiểm tra…'
};

function setStatus(mode) {
  state.mode = mode;

  // Ẩn badge khi ở chế độ demo (không cần hiện "Chế độ demo" lúc nào cũng thấy)
  $('status').hidden = mode === 'demo';

  // Đổi class để CSS áp màu tương ứng
  $('status').className = 'status-badge ' + mode;

  // Đổi text
  $('status-text').textContent = STATUS_TEXT[mode];
}

// Khởi tạo status ban đầu: nếu mock → demo, ngược lại → checking
setStatus(CONFIG.USE_MOCK ? 'demo' : 'checking');

// Nếu dùng backend thật → ping health mỗi 20s
if (!CONFIG.USE_MOCK) {
  const chk = async () => setStatus(await api.checkHealth() ? 'ok' : 'offline');
  chk();                        // Chạy ngay lần đầu
  setInterval(chk, 20000);      // Rồi lặp mỗi 20s
}

/* ----------------------------------------------------------------------------
 * PANEL TRƯỢT — Trích dẫn (≤1200px) và Nguồn (≤860px)
 * ----------------------------------------------------------------------------
 * Trên desktop: 2 panel là 2 cột cố định.
 * Trên tablet/mobile: chuyển thành drawer trượt.
 * -------------------------------------------------------------------------- */

// Mở/đóng panel Trích dẫn (cột phải)
// @param open   — true = mở, false = đóng
// @param manual — true nếu user tự bấm (→ set userToggled để không auto-mở nữa)
function setCitesOpen(open, manual) {
  if (manual) state.userToggled = true;

  // Toggle class trên layout — CSS sẽ animate cột phải
  $('layout').classList.toggle('cites-open', open);

  // inert: panel đóng thì không focus/click được vào bên trong
  $('cites-panel').inert = !open;

  // Cập nhật aria-expanded cho screen reader
  $('cites-toggle').setAttribute('aria-expanded', String(open));
}

// Helper check panel đang mở hay không
const citesOpen = () => $('layout').classList.contains('cites-open');

// Mở/đóng panel Nguồn (cột trái) — chỉ có tác dụng trên mobile
const setSrcOpen = open => $('layout').classList.toggle('src-open', open);

// Gắn event cho nút toggle
$('cites-toggle').onclick = () => setCitesOpen(!citesOpen(), true);   // manual = true
$('cites-close').onclick = () => setCitesOpen(false, true);
$('src-toggle').onclick = () => setSrcOpen(!$('layout').classList.contains('src-open'));

// Click vào scrim (nền mờ) → đóng cả 2 drawer
$('scrim').onclick = () => { setCitesOpen(false, true); setSrcOpen(false); };

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  // Ctrl/Cmd + B → toggle panel Trích dẫn
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    setCitesOpen(!citesOpen(), true);
    return;
  }

  // Escape
  if (e.key === 'Escape') {
    // Nếu đang stream → ưu tiên Dừng trước
    if (state.sending) { stopGen(); return; }

    // Ngược lại: đóng drawer nếu đang mở
    if (innerWidth <= 1200 && citesOpen()) setCitesOpen(false, true);
    setSrcOpen(false);
  }
});

/* ----------------------------------------------------------------------------
 * NGUỒN — Danh sách file (upload + chọn + xóa)
 * -------------------------------------------------------------------------- */

// Map status → function sinh label hiển thị
// uploading cần tham số f để lấy progress
const STATUS_LABEL = {
  uploading: f => 'Đang tải lên... ' + f.progress + '%',
  processing: () => 'Đang xử lý...',
  ready: () => 'Sẵn sàng',
  error: () => 'Không thể tải file'
};

// Render toàn bộ danh sách file (xóa hết rồi vẽ lại — đơn giản, đủ nhanh cho <50 file)
function renderFiles() {
  const list = $('src-list');

  // Xóa sạch DOM cũ (textContent = '' nhanh hơn innerHTML = '')
  list.textContent = '';

  const arr = allFiles();
  const rd = readyFiles();
  const sel = rd.filter(f => f.selected).length;

  // Đếm file trên header panel: "3 file" hoặc rỗng
  $('src-count').textContent = arr.length ? arr.length + ' file' : '';

  // Ẩn "Chọn tất cả" nếu chưa có file ready nào
  $('select-all-row').hidden = !rd.length;

  // Checkbox "Chọn tất cả":
  // - checked: tất cả ready đều được chọn
  // - indeterminate: chọn 1 phần (chỉ có 1 số được chọn)
  $('select-all').checked = rd.length > 0 && sel === rd.length;
  $('select-all').indeterminate = sel > 0 && sel < rd.length;

  // Text "3/5 tài liệu được chọn"
  $('sel-sum').textContent = sel + '/' + rd.length + ' tài liệu được chọn';

  // Nút "Xóa (n) mục đã chọn": chỉ hiện khi có ≥1 file đang được tick.
  // Mỗi lần render lại (kể cả do đổi lựa chọn) đều hủy trạng thái "armed" cũ,
  // tránh trường hợp bấm lần 2 xóa nhầm theo số lượng đã lỗi thời.
  disarmBulkDelete();
  $('bulk-del-btn').hidden = sel === 0;
  $('bulk-del-label').textContent = 'Xóa (' + sel + ')';

  // Số file hiển thị trên badge nút "Nguồn" (mobile)
  $('src-toggle').dataset.n = arr.length;

  // Ẩn note hướng dẫn khi đã có file
  $('src-note').hidden = arr.length > 0;

  // Dropzone thu gọn khi đã có file (CSS .compact)
  $('dropzone').classList.toggle('compact', arr.length > 0);

  // Vẽ từng file
  arr.forEach(f => {
    const el = document.createElement('div');
    el.className = 'src ' + f.status;    // class status → CSS áp màu

    // CHECKBOX: chỉ hiện khi file ready
    if (f.status === 'ready') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = f.selected;
      cb.setAttribute('aria-label', 'Chọn ' + f.name);
      // Khi user tick/untick → cập nhật state + render lại
      cb.onchange = () => { f.selected = cb.checked; renderFiles(); };
      el.appendChild(cb);
    }

    // ICON file (hoặc cảnh báo nếu error)
    const ic = document.createElement('div');
    ic.className = 'ic';
    ic.innerHTML = ico(f.status === 'error' ? 'alert' : 'file-text');
    el.appendChild(ic);

    // PHẦN GIỮA: tên file + size + status
    const mid = document.createElement('div');
    mid.className = 'mid';

    const nm = document.createElement('div');
    nm.className = 'nm';
    nm.title = f.name;                 // Tooltip cho tên dài
    nm.textContent = f.name;
    const st = document.createElement('div');
    st.className = 'st';

    // Size file
    const sz = document.createElement('span');
    sz.textContent = fmtSize(f.size);

    // Status badge (dot + text)
    const ss = document.createElement('span');
    ss.className = 'stt ' + f.status;
    ss.innerHTML = '<i class="dot"></i>';
    ss.append(STATUS_LABEL[f.status](f));  // Gọi function để lấy label
    st.append(sz, ss);
    mid.append(nm, st);

    // PROGRESS BAR: chỉ hiện khi uploading/processing
    if (f.status === 'uploading' || f.status === 'processing') {
      const bar = document.createElement('div');
      // 'busy' → CSS animation shimmer (không có % cụ thể)
      bar.className = 'bar' + (f.status === 'processing' ? ' busy' : '');
      const i = document.createElement('i');
      i.style.width = f.progress + '%';
      bar.appendChild(i);
      mid.appendChild(bar);

      // Hint cho user khi processing
      if (f.status === 'processing') {
        const h = document.createElement('div');
        h.className = 'hint';
        h.textContent = 'Bạn có thể dùng tài liệu sau khi xử lý xong.';
        mid.appendChild(h);
      }
    }

    // NÚT THỬ LẠI: chỉ hiện khi error
    if (f.status === 'error') {
      const rb = document.createElement('button');
      rb.className = 'retry';
      rb.textContent = 'Thử lại';
      rb.onclick = () => startUpload(f.id);   // Gọi lại upload với cùng blob
      mid.appendChild(rb);
    }

    el.appendChild(mid);

    // NÚT XÓA (X)
    const x = document.createElement('button');
    x.className = 'icon-btn x';
    x.innerHTML = ico('x');
    x.title = 'Xóa nguồn';
    x.setAttribute('aria-label', 'Xóa ' + f.name);
    x.onclick = () => {
      f.abort && f.abort();        // Hủy upload nếu đang chạy
      state.files.delete(f.id);    // Xóa khỏi Map
      renderFiles();               // Render lại
    };
    el.appendChild(x);

    list.appendChild(el);
  });

  // Cập nhật composer (context chip + nút gửi)
  updateComposer();
}

// Checkbox "Chọn tất cả" → set selected cho mọi file ready
$('select-all').onchange = e => {
  readyFiles().forEach(f => { f.selected = e.target.checked; });
  renderFiles();
};

/* -----------------------------------------------------------------------
 * NÚT "XÓA (n) MỤC ĐÃ CHỌN" — cùng cơ chế xác nhận 2 bước với nút xóa hội thoại
 * -------------------------------------------------------------------- */
let bulkDelTimer = null;

const disarmBulkDelete = () => {
  clearTimeout(bulkDelTimer);
  const b = $('bulk-del-btn');
  if (b.classList.contains('armed')) {
    b.classList.remove('armed');
    const sel = readyFiles().filter(f => f.selected).length;
    $('bulk-del-label').textContent = 'Xóa (' + sel + ')';
  }
};

$('bulk-del-btn').onclick = () => {
  const b = $('bulk-del-btn');
  const targets = readyFiles().filter(f => f.selected);
  if (!targets.length) return;

  // Lần 1: chuyển sang trạng thái "armed" (chờ xác nhận), tự hủy sau 3s
  if (!b.classList.contains('armed')) {
    b.classList.add('armed');
    $('bulk-del-label').textContent = 'Xóa thật?';
    b.setAttribute('aria-label', 'Bấm lần nữa để xóa ' + targets.length + ' tài liệu đã chọn');
    bulkDelTimer = setTimeout(disarmBulkDelete, 3000);
    return;
  }

  // Lần 2: XÓA THẬT
  clearTimeout(bulkDelTimer);
  targets.forEach(f => { f.abort && f.abort(); state.files.delete(f.id); });
  renderFiles();
  toast('Đã xóa ' + targets.length + ' tài liệu.', 'ok');
};

// Cập nhật khu vực composer: badge trên icon đính kèm + nút Gửi/Dừng
// (Trước đây có chip text "3/5 tài liệu của bạn" ngay trong ô nhập — đã bỏ để composer
// gọn hơn; trạng thái phạm vi giờ chỉ còn 1 badge số nhỏ trên icon đính kèm, chi tiết đầy
// đủ vẫn xem ở panel "Nguồn" bên trái.)
function updateComposer() {
  const rd = readyFiles();
  const sel = rd.filter(f => f.selected).length;
  const badge = $('attach-badge');
  const btn = $('attach-btn');

  if (sel > 0) {
    badge.hidden = false;
    badge.textContent = sel > 99 ? '99+' : sel;
  } else {
    badge.hidden = true;
  }

  // aria-label mô tả đầy đủ phạm vi hiện tại — để không mất thông tin với screen reader
  // dù phần hiển thị bằng mắt chỉ còn 1 con số nhỏ trên badge.
  const scopeDesc = isBusy()
    ? 'đang xử lý tài liệu'
    : !sel ? 'đang tìm trong kho dữ liệu chung' : `đang giới hạn trong ${sel}/${rd.length} tài liệu đã chọn`;
  btn.setAttribute('aria-label', `Thêm file PDF (${scopeDesc})`);
  btn.title = 'Thêm file PDF — ' + scopeDesc;

  syncSend();
}

// Cập nhật trạng thái nút Gửi/Dừng
const syncSend = () => {
  const b = $('send-btn');
  const on = state.sending;   // Có đang chờ assistant không?

  // Nút Dừng (có class .stop đổi màu) khi đang sending
  b.classList.toggle('stop', on);

  // Disable khi: không đang sending VÀ không có text input
  b.disabled = !on && !$('user-input').value.trim();

  // Chỉ đổi icon/title khi mode THAY ĐỔI (tránh reflow mỗi keystroke)
  if (b.dataset.mode !== String(on)) {
    b.dataset.mode = String(on);
    b.innerHTML = ico(on ? 'stop' : 'arrow-up');
    b.title = on ? 'Dừng (Esc)' : 'Gửi';
    b.setAttribute('aria-label', on ? 'Dừng câu trả lời' : 'Gửi câu hỏi');
  }
};

/* ----------------------------------------------------------------------------
 * UPLOAD — Thêm file, validate, gọi api, theo dõi progress
 * -------------------------------------------------------------------------- */

// Thêm danh sách file (từ input change hoặc drag-drop)
async function addFiles(list) {
  for (const blob of list) {
    // CHECK TRÙNG: cùng tên + cùng size + không phải error → coi như trùng
    if (allFiles().some(f => f.status !== 'error' && f.name === blob.name && f.size === blob.size)) {
      toast('"' + blob.name + '" đã có trong danh sách.', 'warn');
      continue;   // Bỏ qua file này, sang file tiếp theo
    }

    // VALIDATE: kiểm tra PDF (4 tầng trong api.js)
    const err = await validateFile(blob);
    if (err) {
      // Lỗi → hiện toast, KHÔNG thêm vào list, KHÔNG gửi backend
      toast(err.message, 'err', 5500, err.title);
      continue;
    }

    // Tạo entry trong state
    const id = uid('file');
    state.files.set(id, {
      id,
      name: blob.name,
      size: blob.size,
      type: 'application/pdf',
      status: 'uploading',    // Bắt đầu ở trạng thái uploading
      progress: 0,
      selected: true,         // Mặc định được chọn
      blob,                   // Giữ File object để có thể retry
      serverId: null          // Chưa có ID từ backend
    });

    // Bắt đầu upload ngay (không chờ các file khác)
    startUpload(id);
  }
}

// Bắt đầu upload file (hoặc retry)
function startUpload(id) {
  const f = state.files.get(id);
  if (!f) return;

  // Reset về trạng thái ban đầu (dùng cho retry)
  Object.assign(f, { status: 'uploading', progress: 0 });
  renderFiles();

  // Gọi API với 2 callbacks
  const h = api.uploadFile(f.blob, {
    // onProgress: cập nhật % và render lại
    onProgress: p => { f.progress = p; renderFiles(); },
    // onProcessing: backend bắt đầu xử lý (extract text, index...)
    onProcessing: () => { f.status = 'processing'; renderFiles(); }
  });

  // Lưu hàm abort để có thể hủy khi user bấm X
  f.abort = h.abort;

  // Xử lý kết quả
  h.promise
    .then(r => {
      // THÀNH CÔNG
      f.status = 'ready';
      f.serverId = r.id;      // ID backend trả về
      f.selected = true;
      renderFiles();
      toast('"' + f.name + '" đã sẵn sàng.', 'ok');
    })
    .catch(e => {
      // User bấm Hủy → không hiện lỗi
      if (e.name === 'AbortError') return;

      // Log chi tiết kỹ thuật chỉ ở console
      console.error('[App] upload error', e);

      f.status = 'error';
      renderFiles();
      toast(
        '"' + f.name + '" chưa tải lên được. Bấm "Thử lại" để tải lại.',
        'err', 5500, 'Không thể tải file'
      );

      // Nếu lỗi mạng + đang dùng real backend → chuyển status offline
      if (e.kind === 'network' && !CONFIG.USE_MOCK) setStatus('offline');
    });
}

// Aliases cho các element upload
const fi = $('file-input');
const dz = $('dropzone');

// Click dropzone → mở file picker
dz.onclick = () => fi.click();

// Enter/Space trên dropzone → mở file picker (accessibility)
dz.onkeydown = e => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fi.click();
  }
};

// Khi user chọn file từ picker
fi.onchange = e => {
  addFiles([...e.target.files]);
  e.target.value = '';   // Reset để có thể chọn lại cùng file
};

// DRAG & DROP
['dragenter', 'dragover'].forEach(t =>
  dz.addEventListener(t, e => { e.preventDefault(); dz.classList.add('drag'); })
);

// Chỉ remove 'drag' khi rời khỏi dropzone (không phải rời khỏi child)
dz.addEventListener('dragleave', e => {
  if (!dz.contains(e.relatedTarget)) dz.classList.remove('drag');
});

// Thả file
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('drag');
  addFiles([...e.dataTransfer.files]);
});

// Chặn browser mở PDF khi user thả nhầm ra ngoài dropzone
['dragover', 'drop'].forEach(t =>
  window.addEventListener(t, e => e.preventDefault())
);

// Nút đính kèm (clip icon) → cũng mở file picker
$('attach-btn').onclick = () => fi.click();

/* ----------------------------------------------------------------------------
 * CHAT — Render message theo status
 * States: pending | streaming | complete | error
 * -------------------------------------------------------------------------- */

// Cuộn xuống cuối chat container
const scrollToBottom = () => {
  const c = $('chat-container');
  c.scrollTop = c.scrollHeight;
};

// Tạo chip citation [n] — click để mở panel + flash
function chipEl(c, m) {
  const k = document.createElement('button');
  k.className = 'chip';
  k.textContent = c.id;
  k.title = c.page != null ? 'Trang ' + c.page : 'Xem nguồn';
  k.setAttribute('aria-label', 'Xem nguồn ' + c.id);
  k.onclick = () => showCite(m.citations, c.id);
  return k;
}

/* ----------------------------------------------------------------------------
 * inline — Parse text inline: **bold**, `code`, *italic*, [n] citation
 * ----------------------------------------------------------------------------
 * Dùng regex split để tách text thành các phần:
 *   - **...**:  bold
 *   - `...`:    inline code
 *   - *...*:    italic (không match nếu có space đầu/cuối)
 *   - [n]:      citation chip
 *
 * Đệ quy: khi gặp bold, gọi inline(st, ...) để parse cả bên trong bold.
 * -------------------------------------------------------------------------- */
function inline(parent, text, m) {
  // Split giữ lại delimiter (capture group trong regex)
  text.split(/(\*\*[^*]+\*\*|`[^`\n]+`|\*(?!\s)[^*\n]+?(?<!\s)\*|\[\d+\])/).forEach(p => {
    if (!p) return;   // Bỏ phần rỗng
    let mm;

    // **bold** → <strong>
    if ((mm = /^\*\*([^*]+)\*\*$/.exec(p))) {
      const st = document.createElement('strong');
      inline(st, mm[1], m);   // Đệ quy để parse nested
      parent.appendChild(st);
    }
    // `code` → <code>
    else if ((mm = /^`([^`]+)`$/.exec(p))) {
      const cd = document.createElement('code');
      cd.textContent = mm[1];
      parent.appendChild(cd);
    }
    // *italic* → <em>
    else if ((mm = /^\*([^*]+)\*$/.exec(p))) {
      const em = document.createElement('em');
      inline(em, mm[1], m);
      parent.appendChild(em);
    }
    // [n] → chip citation (nếu tìm thấy trong m.citations)
    else if ((mm = /^\[(\d+)\]$/.exec(p))) {
      const c = m.citations.find(x => x.id === +mm[1]);
      // +mm[1]: chuyển string → number
      // Nếu tìm thấy → chip, nếu không → giữ nguyên text
      c ? parent.appendChild(chipEl(c, m)) : parent.append(p);
    }
    // Text thường → append trực tiếp
    else parent.append(p);
  });
}

// Check xem 1 cell có phải số không (để căn phải trong bảng)
// Match: "1.5", "-2", "27,3%", "100 tỷ đồng", "5 lần"...
const isNum = t => /^[-+]?[\d.,]+\s*(%|tỷ đồng|triệu đồng|nghìn tỷ|đồng|lần)?$/i.test(t.trim());

// Tách 1 dòng markdown table thành cells
// "| A | B | C |" → ["A", "B", "C"]
const cells = l => l.trim()
  .replace(/^\||\|$/g, '')   // Bỏ | đầu và cuối
  .split('|')                // Split theo |
  .map(c => c.trim());

// Regex nhận diện list item: "- text", "* text", "1. text"
const LI = /^\s*([-*]|\d+\.)\s+(.*)$/;

/* ----------------------------------------------------------------------------
 * richText — Parse markdown block: paragraph, list, table
 * ----------------------------------------------------------------------------
 * Xử lý 3 loại block:
 *   1. TABLE: dòng bắt đầu | và dòng tiếp theo có :?-{2,} (separator)
 *   2. LIST:  dòng match LI regex
 *   3. PARA:  mọi dòng còn lại — gom thành đoạn văn
 * -------------------------------------------------------------------------- */
function richText(b, text, m) {
  const lines = text.split('\n');
  let i = 0;
  let para = [];   // Buffer cho đoạn văn

  // Flush buffer thành <p>
  const flush = () => {
    if (!para.length) return;
    const p = document.createElement('p');
    inline(p, para.join('\n'), m);
    b.appendChild(p);
    para = [];
  };

  while (i < lines.length) {
    const ln = lines[i];

    // === TABLE ===
    // Điều kiện: dòng hiện tại bắt đầu | VÀ dòng tiếp theo là separator
    // Separator: |---|---| hoặc --- hoặc :---:
    if (/^\s*\|/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush();

      // Parse header row
      const head = cells(ln);
      const rows = [];
      i += 2;   // Skip header + separator

      // Parse body rows
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(cells(lines[i++]));
      }

      // Xác định cột nào là số (để căn phải)
      // Cột j là số nếu MỌI row đều có giá trị isNum
      const num = head.map((_, j) =>
        rows.length > 0 && rows.every(r => r[j] !== undefined && isNum(r[j]))
      );

      // Tạo DOM
      const wrap = document.createElement('div');
      const t = document.createElement('table');
      const tr0 = document.createElement('tr');
      const tb = document.createElement('tbody');
      wrap.className = 'tbl';

      // Header
      head.forEach((h, j) => {
        const th = document.createElement('th');
        if (num[j]) th.className = 'num';
        inline(th, h, m);
        tr0.appendChild(th);
      });
      const th = document.createElement('thead');
      th.appendChild(tr0);
      t.appendChild(th);

      // Body
      rows.forEach(r => {
        const tr = document.createElement('tr');
        head.forEach((_, j) => {
          const td = document.createElement('td');
          if (num[j]) td.className = 'num';
          inline(td, r[j] || '', m);
          tr.appendChild(td);
        });
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      wrap.appendChild(t);
      b.appendChild(wrap);

      // Class has-table: bubble max-width 100% thay vì 68ch
      b.classList.add('has-table');
      continue;
    }

    // === LIST ===
    const li = LI.exec(ln);
    if (li) {
      flush();

      // ordered (1. 2. 3.) hay unordered (- * -)?
      const ord = /\d/.test(li[1]);
      const list = document.createElement(ord ? 'ol' : 'ul');

      // Gom các item liên tiếp cùng loại
      while (i < lines.length) {
        const x = LI.exec(lines[i]);
        // Dừng khi: không match HOẶC loại khác (ord ↔ unord)
        if (!x || /\d/.test(x[1]) !== ord) break;

        const it = document.createElement('li');
        inline(it, x[2], m);
        list.appendChild(it);
        i++;
      }
      b.appendChild(list);
      continue;
    }

    // === DÒNG TRỐNG === → kết thúc paragraph
    if (!ln.trim()) { flush(); i++; continue; }

    // === TEXT THƯỜNG === → gom vào paragraph
    para.push(ln);
    i++;
  }

  flush();   // Flush paragraph cuối
}

/* ----------------------------------------------------------------------------
 * buildAfter — Hàng dưới câu trả lời: nút copy + chip nguồn
 * -------------------------------------------------------------------------- */
function buildAfter(m) {
  // Không hiện cho not-found (không có gì để copy/nguồn)
  if (m.notFound) return;

  const af = document.createElement('div');
  af.className = 'after';

  // NÚT COPY
  const cp = document.createElement('button');
  cp.className = 'icon-btn';
  cp.title = 'Sao chép câu trả lời';
  cp.setAttribute('aria-label', 'Sao chép câu trả lời');
  cp.innerHTML = ico('copy');
  cp.onclick = () => {
    navigator.clipboard && navigator.clipboard.writeText(m.content);
    // Đổi icon thành check trong 1.4s
    cp.innerHTML = ico('check');
    setTimeout(() => { cp.innerHTML = ico('copy'); }, 1400);
  };
  af.appendChild(cp);

  // NGUỒN: nếu có citations NHƯNG content không chèn [n] → hiện chip ở dưới
  if (m.citations.length && !/\[\d+\]/.test(m.content)) {
    const sr = document.createElement('div');
    sr.className = 'sources';
    m.citations.forEach(c => {
      const k = document.createElement('button');
      k.className = 'chip';
      k.innerHTML = '<b>' + c.id + '</b>';
      k.append(c.page != null ? 'Trang ' + c.page : 'Nguồn');
      k.onclick = () => showCite(m.citations, c.id);
      sr.appendChild(k);
    });
    af.appendChild(sr);
  }
  // Nếu không có citations → note rõ ràng
  else if (!m.citations.length) {
    const n = document.createElement('span');
    n.className = 'note';
    n.textContent = 'Câu trả lời này chưa có nguồn trích dẫn để đối chiếu.';
    af.appendChild(n);
  }

  m.ct.appendChild(af);
}

/* ----------------------------------------------------------------------------
 * renderMsg — Vẽ 1 message (user hoặc assistant)
 * ----------------------------------------------------------------------------
 * Tối ưu: chỉ tạo DOM lần đầu (khi chưa có m.el).
 * Các lần sau chỉ update nội dung bên trong bubble.
 * -------------------------------------------------------------------------- */
function renderMsg(m) {
  // LẦN ĐẦU: tạo cấu trúc DOM
  if (!m.el) {
    const row = document.createElement('div');
    row.className = 'message-row ' + m.role;   // .user hoặc .assistant

    const ct = document.createElement('div');
    ct.className = 'message-content';

    m.bubble = document.createElement('div');
    m.bubble.className = 'bubble';
    m.ct = ct;   // Lưu reference để buildAfter dùng

    // Avatar chỉ có ở assistant
    if (m.role === 'assistant') {
      const av = document.createElement('div');
      av.className = 'avatar';
      av.innerHTML = ico('chart');
      row.appendChild(av);
    }

    ct.appendChild(m.bubble);
    row.appendChild(ct);
    $('chat-container').appendChild(row);
    m.el = row;
  }

  const b = m.bubble;

  // Xóa 'after' cũ (nếu có) trước khi vẽ lại
  m.ct.querySelector('.after')?.remove();

  // USER message: chỉ set text, xong
  if (m.role === 'user') { b.textContent = m.content; return; }

  // HELPER: chèn caret (con trỏ nhấp nháy) sau chữ cuối
  const caret = () => {
    const s = document.createElement('span');
    s.className = 'caret';

    // Mặc định chèn vào bubble
    let t = b;
    const last = b.lastElementChild;

    // Nếu element cuối là <p> → chèn vào <p>
    if (last && /^P$/.test(last.tagName)) t = last;
    // Nếu là <ul>/<ol> → chèn vào <li> cuối
    else if (last && /^(UL|OL)$/.test(last.tagName) && last.lastElementChild) {
      t = last.lastElementChild;
    }
    // Nếu là table → chèn vào cell cuối cùng của row cuối
    else if (last && last.classList.contains('tbl')) {
      const tr = last.querySelectorAll('tbody tr');
      const cs = tr.length ? tr[tr.length - 1].children : [];
      if (cs.length) t = cs[cs.length - 1];
    }
    t.appendChild(s);
  };

  // RENDER THEO STATUS
  if (m.status === 'pending') {
    // Đang chờ token đầu tiên — hiện dots + label
    b.className = 'bubble pending';
    b.innerHTML = '<span class="dots"><i></i><i></i><i></i></span><span class="pl"></span>';
    b.querySelector('.pl').textContent = m.label || 'Đang tìm câu trả lời…';
  }
  else if (m.status === 'error') {
    // LỖI HỆ THỐNG (khác not_found) — bubble đỏ + nút Thử lại
    b.className = 'bubble error';
    b.innerHTML = ico('alert');
    const sp = document.createElement('span');
    sp.textContent = m.errKind === 'network'
      ? 'Không thể kết nối máy chủ. Vui lòng thử lại.'
      : 'Đã xảy ra lỗi khi xử lý câu hỏi. Vui lòng thử lại.';
    b.appendChild(sp);

    const rb = document.createElement('button');
    rb.className = 'retry';
    rb.textContent = 'Thử lại';
    rb.onclick = () => retry(m);
    b.appendChild(rb);
  }
  else {
    // STREAMING hoặc COMPLETE — render bằng richText
    b.className = 'bubble';
    b.textContent = '';   // Xóa sạch (richText chỉ append)
    richText(b, m.content, m);

    // Streaming → caret; Complete → after
    if (m.status === 'streaming') caret();
    else buildAfter(m);
  }
}

/* ----------------------------------------------------------------------------
 * run — Chạy 1 lượt chat: gửi request + nhận stream
 * ----------------------------------------------------------------------------
 * @param m — assistant message object (đã push vào state.messages)
 * -------------------------------------------------------------------------- */
async function run(m) {
  state.sending = true;
  state.active = m;   // Để stopGen biết cần abort message nào

  // Reset state của message
  Object.assign(m, {
    status: 'pending', content: '', citations: [],
    label: '', errKind: null, notFound: false
  });

  renderMsg(m);
  syncSend();
  scrollToBottom();

  try {
    await api.sendChat(m.request, {
      // AbortController cho phép user bấm Dừng
      signal: (m.ctrl = new AbortController()).signal,

      // Callback lấy tên file từ ID (để hiện trong citation)
      sourceName: id => (allFiles().find(f => refOf(f) === id) || {}).name,

      // onStatus: chỉ update khi còn pending (tránh ghi đè khi đã stream)
      onStatus: s => {
        if (m.status === 'pending') { m.label = s; renderMsg(m); }
      },

      // onNotFound: chỉ set cờ, không thay đổi UI ngay
      onNotFound: () => { m.notFound = true; },

      // onCitations: lưu vào message + render panel
      onCitations: cs => {
        m.citations = cs;
        renderCites(cs);
        // Auto-mở panel trên desktop NẾU user chưa tự tắt
        if (cs.length && !state.userToggled && innerWidth > 1200) setCitesOpen(true);
      },

      // onToken: append content + render lại
      onToken: t => {
        m.status = 'streaming';
        m.content += t;
        renderMsg(m);
        scrollToBottom();
      },
    });

    // STREAM THÀNH CÔNG
    m.status = 'complete';

    // Nếu không có content → coi như not found
    if (!m.content.trim()) {
      m.notFound = true;
      m.content = m.request.sourceIds.length
        ? 'Tôi không tìm thấy thông tin liên quan trong các tài liệu bạn đã chọn.'
        : 'Tôi không tìm thấy thông tin liên quan trong dữ liệu hiện có.';
    }
    m.content = m.content.trim();

    // Nếu backend tự trả câu "không tìm thấy" → set notFound để ẩn after
    if (!m.citations.length && /không tìm thấy/i.test(m.content)) m.notFound = true;

    // Nếu real backend → chuyển status về ok (đã kết nối thành công)
    if (!CONFIG.USE_MOCK) setStatus('ok');
  }
  catch (e) {
    if (e.name === 'AbortError') {
      // USER BẤM DỪNG: giữ phần đã nhận
      const had = m.content.trim();
      m.status = 'complete';
      m.content = had || 'Đã dừng trước khi có câu trả lời.';
      if (!had) m.notFound = true;   // Không có gì → coi như not found
    } else {
      // LỖI THẬT: log console, hiện bubble error
      console.error('[App] chat error', e);
      m.status = 'error';
      m.errKind = e.kind;
      if (e.kind === 'network' && !CONFIG.USE_MOCK) setStatus('offline');
    }
  }
  finally {
    // Luôn chạy sau try/catch — dù thành công, lỗi, hay abort
    state.sending = false;
    renderMsg(m);
    syncSend();
    scrollToBottom();
    $('user-input').focus();   // Focus lại input cho user gõ tiếp
  }
}

// Retry 1 message bị lỗi
const retry = m => { if (!state.sending) run(m); };

/* ----------------------------------------------------------------------------
 * submit — Gửi câu hỏi mới
 * ----------------------------------------------------------------------------
 * @param text — text (nếu gọi từ suggestion pill), mặc định lấy từ input
 * -------------------------------------------------------------------------- */
function submit(text) {
  if (state.sending) return;   // Đang chờ → không cho gửi

  const q = (text ?? $('user-input').value).trim();
  if (!q) return;   // Rỗng → bỏ qua

  // Lấy IDs của file đang được chọn
  // Rỗng = hỏi trên DB chung, api sẽ không gửi file_ids
  const ids = readyFiles().filter(f => f.selected).map(refOf);

  // Clear input + reset height
  $('user-input').value = '';
  $('user-input').style.height = 'auto';

  // Tạo 2 message: user + assistant
  const u = { id: uid('msg'), role: 'user', content: q, status: 'complete' };
  const a = {
    id: uid('msg'),
    role: 'assistant',
    content: '',
    status: 'pending',
    citations: [],
    request: { message: q, sourceIds: ids }   // Request để gửi lên api
  };

  state.messages.push(u, a);
  renderMsg(u);
  renderMsg(a);
  run(a);   // Chạy ngay
}

// Dừng generation hiện tại
function stopGen() {
  if (state.active && state.active.ctrl) state.active.ctrl.abort();
}

// Nút Gửi: nếu đang sending → Dừng, ngược lại → Gửi
$('send-btn').onclick = () => state.sending ? stopGen() : submit();

// Enter (không Shift) → Gửi; Shift+Enter → xuống dòng
// e.isComposing: bỏ qua khi đang gõ tiếng Trung/Nhật/Hàn (IME)
$('user-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  }
});

// Auto-resize textarea theo nội dung (max 140px)
$('user-input').addEventListener('input', e => {
  const t = e.target;
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight, 140) + 'px';
  syncSend();   // Cập nhật trạng thái nút Gửi
});

/* ----------------------------------------------------------------------------
 * NÚT XÓA HỘI THOẠI — Yêu cầu bấm 2 lần (chống xóa nhầm)
 * -------------------------------------------------------------------------- */
let clearTimer = null;

// Reset nút về trạng thái bình thường
const disarmClear = () => {
  clearTimeout(clearTimer);
  const b = $('clear-btn');
  b.classList.remove('armed');
  b.innerHTML = ico('trash');
  b.setAttribute('aria-label', 'Xóa hội thoại');
  b.title = 'Xóa hội thoại';
};

$('clear-btn').onclick = () => {
  // Đang chờ trả lời → không cho xóa
  if (state.sending) return toast('Đợi câu trả lời hoàn tất rồi hãy xóa.', 'warn');

  const b = $('clear-btn');

  // Lần 1: chuyển sang trạng thái "armed" (chờ xác nhận)
  if (!b.classList.contains('armed')) {
    b.classList.add('armed');
    b.innerHTML = ico('trash') + '<span>Xóa?</span>';
    b.setAttribute('aria-label', 'Bấm lần nữa để xóa hội thoại');
    b.title = 'Bấm lần nữa để xóa';
    // Tự hủy sau 3s nếu user không bấm lần 2
    clearTimer = setTimeout(disarmClear, 3000);
    return;
  }

  // Lần 2: XÓA THẬT
  disarmClear();
  state.messages = [];
  // Xóa các message-row trừ message chào mừng (có data-static)
  document.querySelectorAll('#chat-container .message-row:not([data-static])')
    .forEach(n => n.remove());
  renderCites([]);
  if (!state.userToggled) setCitesOpen(false);
};

/* ----------------------------------------------------------------------------
 * TRÍCH DẪN — Panel bên phải
 * -------------------------------------------------------------------------- */

// Click chip [n] → mở panel + scroll đến cite + flash
function showCite(list, n) {
  // Nếu list khác với list hiện tại → render lại
  if (state.cites !== list) renderCites(list);

  setCitesOpen(true);

  // Đợi render xong rồi mới scroll + flash
  requestAnimationFrame(() => {
    const el = $('cite-' + n);
    if (!el) return;
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

    // Restart animation bằng cách remove → reflow → add
    el.classList.remove('flash');
    void el.offsetWidth;   // Force reflow
    el.classList.add('flash');
  });
}

// Tạo 1 card citation
function citeCard(c) {
  const el = document.createElement('div');
  el.className = 'cite';
  el.id = 'cite-' + c.id;

  // Header: số + trang
  const top = document.createElement('div');
  top.className = 'top';
  const num = document.createElement('span');
  num.className = 'n';
  num.textContent = c.id;
  top.appendChild(num);

  if (c.page != null) {
    const p = document.createElement('span');
    p.className = 'pg';
    p.textContent = 'Trang ' + c.page;
    top.appendChild(p);
  }
  el.appendChild(top);

  // Excerpt
  if (c.excerpt) {
    // Nếu excerpt > 170 ký tự → collapse
    const long = c.excerpt.length > 170;
    const s = document.createElement('div');
    s.className = 's' + (long ? ' long' : '');
    s.textContent = c.excerpt;
    el.appendChild(s);

    // Nút expand/collapse
    if (long) {
      const mo = document.createElement('span');
      mo.className = 'more';
      mo.innerHTML = ico('chevron');
      el.appendChild(mo);

      // Card trở thành button — click để toggle
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.onkeydown = e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          el.click();
        }
      };
      el.onclick = () => {
        el.classList.toggle('open');
        s.classList.toggle('open');
      };
    }
  }

  // Card ngắn vẫn focusable (accessibility)
  if (!el.hasAttribute('tabindex')) {
    el.tabIndex = 0;
    el.setAttribute('aria-label',
      'Nguồn ' + c.id + (c.page != null ? ', trang ' + c.page : ''));
  }

  return el;
}

// Render toàn bộ panel citations
function renderCites(list) {
  state.cites = list;
  const box = $('cites');
  box.textContent = '';

  // Update badge số lượng
  $('cite-count').textContent = list.length;
  $('cite-count').hidden = !list.length;

  // EMPTY STATE
  if (!list.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.innerHTML = ico('book') +
      '<b>Chưa có trích dẫn</b>Đoạn trích và số trang sẽ hiện ở đây sau mỗi câu trả lời.';
    box.appendChild(e);
    return;
  }

  // GROUP theo sourceId (hoặc source name nếu không có sourceId)
  const groups = new Map();
  list.forEach(c => {
    const k = c.sourceId ?? c.source;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(c);
  });

  // Render từng group
  groups.forEach(arr => {
    const g = document.createElement('section');
    g.className = 'cg';

    // Header: icon + tên file
    const h = document.createElement('div');
    h.className = 'cg-h';
    h.innerHTML = ico('file-text');

    const t = document.createElement('span');
    t.textContent = arr[0].source;
    t.title = arr[0].source;
    h.appendChild(t);
    g.appendChild(h);

    // Các card trong group
    arr.forEach(c => g.appendChild(citeCard(c)));
    box.appendChild(g);
  });
}

// Khởi tạo giao diện
renderCites([]);        // Panel citations rỗng (empty state)
renderFiles();          // Panel files rỗng (chỉ có dropzone)
setCitesOpen(false);    // Panel citations đóng mặc định