/* ── Helpers ────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const show = id => $(id).classList.remove('hidden');
const hide = id => $(id).classList.add('hidden');

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Erreur serveur' }));
    const e = new Error(err.detail || 'Erreur inconnue');
    e.status = res.status;
    throw e;
  }
  return res;
}

function showError(id, msg) { const el = $(id); el.textContent = msg; el.classList.remove('hidden'); }
function clearError(id) { $(id).classList.add('hidden'); }

/* ── Couleurs graphes ───────────────────────────────────────────────── */
const PALETTE = ['#2e7dd1', '#1a4b8c', '#1a7a4a', '#d1862e', '#8c1a4b',
                 '#0f2f5c', '#4ba3d1', '#7a1a1a', '#5a6a7e', '#2ed1a3'];
const charts = {};

function renderBar(canvasId, data, label) {
  const ctx = $(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map(d => d.label),
      datasets: [{ label, data: data.map(d => d.count), backgroundColor: '#2e7dd1', borderRadius: 4 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function renderDoughnut(canvasId, data) {
  const ctx = $(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: data.map(d => d.label),
      datasets: [{ data: data.map(d => d.count), backgroundColor: PALETTE }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'right' } },
    },
  });
}

function renderLine(canvasId, data) {
  const ctx = $(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map(d => d.label),
      datasets: [{
        label: 'Rapports', data: data.map(d => d.count),
        borderColor: '#2e7dd1', backgroundColor: 'rgba(46,125,209,.12)',
        fill: true, tension: .25, pointRadius: 3,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

/* ── Chargement des stats ───────────────────────────────────────────── */
async function loadStats() {
  const stats = (await (await api('GET', '/api/stats')).json());
  $('kpi-total').textContent = stats.total;
  $('kpi-users').textContent = stats.by_user.length;
  $('kpi-types').textContent = stats.by_type.length;
  renderBar('chart-user', stats.by_user, 'Rapports');
  renderDoughnut('chart-type', stats.by_type);
  renderLine('chart-day', stats.by_day);

  const events = (await (await api('GET', '/api/events')).json()).events;
  const body = $('events-body');
  body.innerHTML = '';
  if (!events.length) { show('events-empty'); }
  else {
    hide('events-empty');
    for (const e of events) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        `<td>${e.date}</td><td>${e.time}</td><td>${escapeHtml(e.username)}</td>` +
        `<td><span class="tag">${escapeHtml(e.project_type)}</span></td>` +
        `<td>${e.n_chapters}</td><td>${e.n_annexes}</td>`;
      body.appendChild(tr);
    }
  }
}

/* ── Gestion des utilisateurs ───────────────────────────────────────── */
async function loadUsers() {
  const users = (await (await api('GET', '/api/users')).json()).users;
  const body = $('users-body');
  body.innerHTML = '';
  for (const u of users) {
    const isAdmin = u.role === 'admin';
    const tr = document.createElement('tr');
    const actions = isAdmin
      ? '<span class="empty-msg" style="padding:0;">—</span>'
      : `<div class="row-actions">
           <button class="btn-mini" data-act="rename" data-id="${u.id}" data-name="${escapeAttr(u.username)}">Renommer</button>
           <button class="btn-mini" data-act="password" data-id="${u.id}" data-name="${escapeAttr(u.username)}">Mot de passe</button>
           <button class="btn-mini danger" data-act="delete" data-id="${u.id}" data-name="${escapeAttr(u.username)}">Supprimer</button>
         </div>`;
    tr.innerHTML =
      `<td>${escapeHtml(u.username)}</td>` +
      `<td><span class="role-badge ${u.role}">${u.role}</span></td>` +
      `<td>${u.created_at}</td><td>${actions}</td>`;
    body.appendChild(tr);
  }
}

$('form-create').addEventListener('submit', async e => {
  e.preventDefault();
  clearError('users-error');
  const username = $('new-username').value.trim();
  const password = $('new-password').value;
  try {
    await api('POST', '/api/users', { username, password });
    $('new-username').value = '';
    $('new-password').value = '';
    await loadUsers();
  } catch (err) {
    showError('users-error', err.message);
  }
});

$('users-body').addEventListener('click', async e => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  clearError('users-error');
  const { act, id, name } = btn.dataset;
  try {
    if (act === 'rename') {
      const v = prompt(`Nouvel identifiant pour « ${name} » :`, name);
      if (v == null || !v.trim()) return;
      await api('PATCH', `/api/users/${id}/username`, { username: v.trim() });
    } else if (act === 'password') {
      const v = prompt(`Nouveau mot de passe pour « ${name} » :`);
      if (v == null || !v) return;
      await api('PATCH', `/api/users/${id}/password`, { password: v });
    } else if (act === 'delete') {
      if (!confirm(`Supprimer définitivement le compte « ${name} » ?`)) return;
      await api('DELETE', `/api/users/${id}`);
    }
    await loadUsers();
  } catch (err) {
    showError('users-error', err.message);
  }
});

/* ── Onglets ────────────────────────────────────────────────────────── */
document.querySelectorAll('.admin-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const name = tab.dataset.tab;
    $('tab-stats').classList.toggle('hidden', name !== 'stats');
    $('tab-users').classList.toggle('hidden', name !== 'users');
  });
});

/* ── Déconnexion ────────────────────────────────────────────────────── */
$('btn-logout').addEventListener('click', async () => {
  await api('POST', '/api/logout').catch(() => {});
  location.href = 'index.html';
});

/* ── Échappement ────────────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

/* ── Bootstrap : vérifie le rôle admin ──────────────────────────────── */
(async () => {
  try {
    const me = await (await api('GET', '/api/me')).json();
    if (me.role !== 'admin') {
      showError('access-error', 'Accès réservé à l\'administrateur. Redirection…');
      setTimeout(() => location.href = 'index.html', 1500);
      return;
    }
    show('admin-content');
    await loadStats();
    await loadUsers();
  } catch (err) {
    if (err.status === 401) { location.href = 'index.html'; return; }
    showError('access-error', err.message);
  }
})();
