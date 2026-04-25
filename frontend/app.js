/* ── State ──────────────────────────────────────────────────────────── */
const state = {
  sessionId: null,
  structure: [],
  selected: new Set(),
  filename: '',
};

/* ── DOM helpers ────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const show = id => $(id).classList.remove('hidden');
const hide = id => $(id).classList.add('hidden');

function showView(name) {
  ['login', 'upload', 'select'].forEach(v => {
    document.getElementById(`view-${v}`).classList.toggle('hidden', v !== name);
  });
}

function showError(id, msg) {
  const el = $(id);
  el.textContent = msg;
  el.classList.remove('hidden');
}

function clearError(id) {
  $(id).classList.add('hidden');
}

/* ── API ────────────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const opts = {
    method,
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  };
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Erreur serveur' }));
    throw new Error(err.detail || 'Erreur inconnue');
  }
  return res;
}

/* ── Login ──────────────────────────────────────────────────────────── */
$('form-login').addEventListener('submit', async e => {
  e.preventDefault();
  clearError('login-error');
  const username = $('username').value.trim();
  const password = $('password').value;
  try {
    await api('POST', '/api/login', { username, password });
    showView('upload');
  } catch (err) {
    showError('login-error', err.message);
  }
});

async function logout() {
  await api('POST', '/api/logout').catch(() => {});
  showView('login');
  $('username').value = '';
  $('password').value = '';
}

$('btn-logout').addEventListener('click', logout);
$('btn-logout2').addEventListener('click', logout);

/* ── Check auth on load ─────────────────────────────────────────────── */
(async () => {
  try {
    await api('GET', '/api/me');
    showView('upload');
  } catch {
    showView('login');
  }
})();

/* ── Upload ─────────────────────────────────────────────────────────── */
const dropZone = $('drop-zone');
const fileInput = $('file-input');

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});
dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  if (!file.name.endsWith('.docx')) {
    showError('upload-error', 'Seuls les fichiers .docx sont acceptés.');
    return;
  }
  clearError('upload-error');
  show('upload-loading');
  dropZone.style.pointerEvents = 'none';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await api('POST', '/api/upload', form);
    const data = await res.json();
    state.sessionId = data.session_id;
    state.structure = data.structure;
    state.filename = file.name;
    renderChapterList();
    showView('select');
    $('filename-label').textContent = file.name;
  } catch (err) {
    showError('upload-error', err.message);
  } finally {
    hide('upload-loading');
    dropZone.style.pointerEvents = '';
    fileInput.value = '';
  }
}

/* ── Chapter list ───────────────────────────────────────────────────── */
function renderChapterList() {
  const container = $('chapter-list');
  container.innerHTML = '';
  state.selected.clear();

  // Pré-sélectionner tout
  state.structure.forEach(item => {
    if (item.type !== 'section') state.selected.add(item.id);
  });

  // Grouper par section
  let currentGroup = null;
  let currentSection = null;

  state.structure.forEach(item => {
    if (item.type === 'section') {
      currentSection = item.label;
      currentGroup = null;
      return;
    }

    // Créer un groupe si nécessaire
    if (!currentGroup || currentGroup.dataset.section !== currentSection) {
      const groupEl = document.createElement('div');
      groupEl.className = 'section-group';
      groupEl.dataset.section = currentSection || '';

      if (currentSection) {
        const label = document.createElement('div');
        label.className = 'section-label';
        label.textContent = currentSection;
        groupEl.appendChild(label);
      }
      container.appendChild(groupEl);
      currentGroup = groupEl;
    }

    currentGroup.appendChild(buildItem(item));
  });

  updateCount();
}

function buildItem(item) {
  const row = document.createElement('label');
  row.className = 'chapter-item selected';
  row.dataset.id = item.id;

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = true;
  cb.dataset.id = item.id;

  const badge = document.createElement('span');
  badge.className = item.type === 'annex' ? 'chapter-num annex' : 'chapter-num';
  badge.textContent = item.type === 'annex' ? `A${item.num}` : item.num;

  const lbl = document.createElement('span');
  lbl.className = 'chapter-label';
  lbl.textContent = item.label;

  cb.addEventListener('change', () => toggleItem(item.id, cb.checked, row));

  row.appendChild(cb);
  row.appendChild(badge);
  row.appendChild(lbl);
  return row;
}

function toggleItem(id, checked, row) {
  if (checked) {
    state.selected.add(id);
    row.classList.add('selected');
  } else {
    state.selected.delete(id);
    row.classList.remove('selected');
  }
  updateCount();
}

function updateCount() {
  const n = state.selected.size;
  $('count-label').textContent = `${n} élément${n > 1 ? 's' : ''} sélectionné${n > 1 ? 's' : ''}`;
  $('btn-generate').disabled = n === 0;
}

$('btn-select-all').addEventListener('click', () => {
  document.querySelectorAll('.chapter-item input[type="checkbox"]').forEach(cb => {
    cb.checked = true;
    state.selected.add(cb.dataset.id);
    cb.closest('.chapter-item').classList.add('selected');
  });
  updateCount();
});

$('btn-deselect-all').addEventListener('click', () => {
  document.querySelectorAll('.chapter-item input[type="checkbox"]').forEach(cb => {
    cb.checked = false;
    state.selected.delete(cb.dataset.id);
    cb.closest('.chapter-item').classList.remove('selected');
  });
  updateCount();
});

$('btn-new-file').addEventListener('click', () => {
  state.sessionId = null;
  state.structure = [];
  state.selected.clear();
  showView('upload');
});

/* ── Generate ───────────────────────────────────────────────────────── */
$('btn-generate').addEventListener('click', async () => {
  clearError('generate-error');
  show('generate-loading');
  $('btn-generate').disabled = true;

  try {
    const res = await api('POST', '/api/generate', {
      session_id: state.sessionId,
      selected_ids: [...state.selected],
    });

    // Déclencher le téléchargement
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'rapport_filtre.docx';
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showError('generate-error', err.message);
  } finally {
    hide('generate-loading');
    $('btn-generate').disabled = state.selected.size === 0;
  }
});
