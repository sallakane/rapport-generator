/* ── State ──────────────────────────────────────────────────────────── */
const state = {
  structure: null,                  // { sections, annexes }
  chapters: new Set(),              // ids cochés (h1_*, h2_*, h3_*)
  annexes: new Set(),               // numéros cochés
  parentOf: new Map(),              // childId → parentId (h2 → h1, h3 → h2)
  childrenOf: new Map(),            // parentId → [childId...]
};

/* ── DOM helpers ────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const show = id => $(id).classList.remove('hidden');
const hide = id => $(id).classList.add('hidden');

function showView(name) {
  ['login', 'select'].forEach(v => {
    $(`view-${v}`).classList.toggle('hidden', v !== name);
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
    enterApp();
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

/* ── Bootstrap ──────────────────────────────────────────────────────── */
(async () => {
  try {
    await api('GET', '/api/me');
    enterApp();
  } catch {
    showView('login');
  }
})();

async function enterApp() {
  showView('select');
  show('loading-structure');
  hide('content-pane');
  try {
    const res = await api('GET', '/api/structure');
    state.structure = await res.json();
    buildIndex();
    renderTree();
    renderAnnexes();
    selectAll();
    hide('loading-structure');
    show('content-pane');
  } catch (err) {
    showError('generate-error', `Impossible de charger la structure : ${err.message}`);
    hide('loading-structure');
  }
}

/* ── Index parent/enfant pour la cascade ────────────────────────────── */
function buildIndex() {
  state.parentOf.clear();
  state.childrenOf.clear();
  for (const sec of state.structure.sections) {
    for (const h1 of sec.chapters || []) {
      state.childrenOf.set(h1.id, []);
      for (const h2 of h1.children || []) {
        state.parentOf.set(h2.id, h1.id);
        state.childrenOf.get(h1.id).push(h2.id);
        state.childrenOf.set(h2.id, []);
        for (const h3 of h2.children || []) {
          state.parentOf.set(h3.id, h2.id);
          state.childrenOf.get(h2.id).push(h3.id);
        }
      }
    }
  }
}

/* ── Rendu de l'arbre ───────────────────────────────────────────────── */
function renderTree() {
  const root = $('chapter-tree');
  root.innerHTML = '';
  for (const sec of state.structure.sections) {
    if (!sec.chapters || sec.chapters.length === 0) continue; // TOC : on n'affiche pas
    const groupEl = document.createElement('div');
    groupEl.className = 'section-group';

    const lbl = document.createElement('div');
    lbl.className = 'section-label';
    lbl.textContent = sec.label;
    groupEl.appendChild(lbl);

    for (const h1 of sec.chapters) {
      groupEl.appendChild(buildChapterNode(h1, 1));
    }
    root.appendChild(groupEl);
  }
}

function buildChapterNode(node, level) {
  const wrap = document.createElement('div');
  wrap.className = `tree-node level-${level}`;

  const row = document.createElement('label');
  row.className = 'chapter-item';
  row.dataset.id = node.id;

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.dataset.id = node.id;
  cb.addEventListener('change', () => onChapterToggle(node.id, cb.checked));

  const lbl = document.createElement('span');
  lbl.className = 'chapter-label';
  lbl.textContent = node.label;

  row.appendChild(cb);
  row.appendChild(lbl);
  wrap.appendChild(row);

  for (const child of node.children || []) {
    wrap.appendChild(buildChapterNode(child, level + 1));
  }
  return wrap;
}

function renderAnnexes() {
  const root = $('annex-list');
  root.innerHTML = '';
  for (const a of state.structure.annexes) {
    const row = document.createElement('label');
    row.className = 'chapter-item annex-row';
    row.dataset.num = a.num;

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.num = a.num;
    cb.addEventListener('change', () => onAnnexToggle(a.num, cb.checked));

    const badge = document.createElement('span');
    badge.className = 'chapter-num annex';
    badge.textContent = `A${a.num}`;

    const lbl = document.createElement('span');
    lbl.className = 'chapter-label';
    lbl.textContent = a.label;

    row.appendChild(cb);
    row.appendChild(badge);
    row.appendChild(lbl);
    root.appendChild(row);
  }
}

/* ── Cascade ────────────────────────────────────────────────────────── */
function onChapterToggle(id, checked) {
  if (checked) {
    state.chapters.add(id);
    // Cascade descendante : cocher tous les enfants
    for (const child of descendants(id)) state.chapters.add(child);
    // Cascade montante : forcer les ancêtres cochés
    for (let p = state.parentOf.get(id); p; p = state.parentOf.get(p)) {
      state.chapters.add(p);
    }
  } else {
    state.chapters.delete(id);
    // Cascade descendante : décocher tous les enfants
    for (const child of descendants(id)) state.chapters.delete(child);
  }
  syncChaptersToDOM();
  updateCount();
}

function onAnnexToggle(num, checked) {
  if (checked) state.annexes.add(num);
  else state.annexes.delete(num);
  updateCount();
}

function descendants(id) {
  const out = [];
  const stack = [...(state.childrenOf.get(id) || [])];
  while (stack.length) {
    const x = stack.pop();
    out.push(x);
    for (const c of state.childrenOf.get(x) || []) stack.push(c);
  }
  return out;
}

function syncChaptersToDOM() {
  document.querySelectorAll('#chapter-tree input[type="checkbox"]').forEach(cb => {
    const id = cb.dataset.id;
    const on = state.chapters.has(id);
    cb.checked = on;
    cb.closest('.chapter-item').classList.toggle('selected', on);
  });
}

function syncAnnexesToDOM() {
  document.querySelectorAll('#annex-list input[type="checkbox"]').forEach(cb => {
    const num = parseInt(cb.dataset.num, 10);
    const on = state.annexes.has(num);
    cb.checked = on;
    cb.closest('.chapter-item').classList.toggle('selected', on);
  });
}

/* ── Compteurs / actions globales ───────────────────────────────────── */
function updateCount() {
  // On compte uniquement les "feuilles" et les annexes pour donner un nombre parlant
  const leafChapters = [...state.chapters].filter(id => !(state.childrenOf.get(id)?.length));
  const n = leafChapters.length + state.annexes.size;
  $('count-label').textContent = `${n} élément${n > 1 ? 's' : ''} sélectionné${n > 1 ? 's' : ''}`;
  $('btn-generate').disabled = state.chapters.size === 0 && state.annexes.size === 0;
}

function selectAll() {
  state.chapters.clear();
  for (const id of state.parentOf.keys()) state.chapters.add(id);
  for (const id of state.childrenOf.keys()) state.chapters.add(id);
  state.annexes.clear();
  for (const a of state.structure.annexes) state.annexes.add(a.num);
  syncChaptersToDOM();
  syncAnnexesToDOM();
  updateCount();
}

function deselectAll() {
  state.chapters.clear();
  state.annexes.clear();
  syncChaptersToDOM();
  syncAnnexesToDOM();
  updateCount();
}

$('btn-select-all').addEventListener('click', selectAll);
$('btn-deselect-all').addEventListener('click', deselectAll);

/* ── Progress (simulée) ─────────────────────────────────────────────── */
let progressTimer = null;
let progressStart = 0;
let progressTau = 30;

function setProgress(p) {
  $('progress-bar').style.width = `${p}%`;
  $('progress-percent').textContent = `${Math.round(p)} %`;
}

function startProgress(itemCount) {
  // ETA empirique : ~0.75 s/item + 5 s d'overhead (100 items ≈ 60-80 s,
  // 154 items ≈ 120 s d'après les mesures terrain).
  // On vise ~85 % de la barre à t=ETA, asymptote à 90 %.
  const etaSeconds = Math.max(8, 5 + 0.75 * itemCount);
  progressTau = etaSeconds / 2.89;
  progressStart = performance.now();
  setProgress(0);
  show('generate-overlay');
  progressTimer = setInterval(() => {
    const elapsed = (performance.now() - progressStart) / 1000;
    setProgress(90 * (1 - Math.exp(-elapsed / progressTau)));
  }, 200);
}

function stopProgress(success) {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  if (success) {
    setProgress(100);
    setTimeout(() => hide('generate-overlay'), 350);
  } else {
    hide('generate-overlay');
  }
}

/* ── Generate ───────────────────────────────────────────────────────── */
$('btn-generate').addEventListener('click', async () => {
  clearError('generate-error');
  $('btn-generate').disabled = true;
  const leafCount = [...state.chapters].filter(id => !(state.childrenOf.get(id)?.length)).length;
  startProgress(leafCount + state.annexes.size);

  try {
    const res = await api('POST', '/api/generate', {
      chapters: [...state.chapters],
      annexes: [...state.annexes],
    });
    const blob = await res.blob();
    stopProgress(true);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'rapport_filtre.docx';
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    stopProgress(false);
    showError('generate-error', err.message);
  } finally {
    updateCount();
  }
});
