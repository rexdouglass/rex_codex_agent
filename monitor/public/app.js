const els = {
  connDot: document.getElementById('connDot'),
  epm: document.getElementById('eventsPerMin'),
  totAll: document.getElementById('totAll'),
  totInfo: document.getElementById('totInfo'),
  totWarn: document.getElementById('totWarn'),
  totErr: document.getElementById('totErr'),
  totTask: document.getElementById('totTask'),
  lastEvent: document.getElementById('lastEvent'),
  tasks: document.getElementById('tasks'),
  log: document.getElementById('log'),
  errors: document.getElementById('errors'),
  filters: document.querySelectorAll('.filters input[type="checkbox"]'),
  searchBox: document.getElementById('searchBox')
};

const state = {
  filters: new Set(['info', 'warn', 'error']),
  query: ''
};

els.filters.forEach(cb => {
  cb.addEventListener('change', () => {
    if (cb.checked) state.filters.add(cb.value);
    else state.filters.delete(cb.value);
    renderLog();
  });
});
els.searchBox.addEventListener('input', () => {
  state.query = els.searchBox.value.trim().toLowerCase();
  renderLog();
});

const logItems = []; // {ts, level, message, task, status, progress}
const taskState = {}; // taskName -> {lastStatus, progress, count, lastAt}

function fmtTs(iso) {
  try {
    const d = new Date(iso);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  } catch {
    return iso;
  }
}

function renderSummary(s) {
  els.epm.textContent = `${s.eventsPerMinute} evt/min`;
  els.totAll.textContent = s.totals.all || 0;
  els.totInfo.textContent = s.totals.info || 0;
  els.totWarn.textContent = s.totals.warn || 0;
  els.totErr.textContent = s.totals.error || 0;
  els.totTask.textContent = s.totals.task || 0;
  els.lastEvent.textContent = 'Last event: ' + (s.lastEventAt ? new Date(s.lastEventAt).toLocaleString() : '—');

  // tasks
  Object.assign(taskState, s.tasks || {});
  renderTasks();
  renderErrors(s.lastErrors || []);
}

function renderTasks() {
  const tasks = Object.entries(taskState)
    .sort((a, b) => (b[1].lastAt || '').localeCompare(a[1].lastAt || ''));
  const frag = document.createDocumentFragment();
  for (const [name, t] of tasks) {
    const wrap = document.createElement('div');
    wrap.className = 'task-card';
    wrap.innerHTML = `
      <div class="task-row">
        <div>
          <div class="task-name">${escapeHtml(name)}</div>
          <div class="task-meta">${t.lastStatus || '—'} · last: ${t.lastAt ? new Date(t.lastAt).toLocaleTimeString() : '—'} · seen: ${t.count || 0}</div>
        </div>
        <div style="min-width:120px;text-align:right;">${t.progress != null ? Math.round(t.progress * 100) + '%' : ''}</div>
      </div>
      <div class="bar"><span style="width:${Math.max(0, Math.min(100, Math.round((t.progress || 0) * 100)))}%"></span></div>
    `;
    frag.appendChild(wrap);
  }
  els.tasks.innerHTML = '';
  els.tasks.appendChild(frag);
}

function renderErrors(errs) {
  const frag = document.createDocumentFragment();
  for (const e of errs.slice().reverse()) {
    const div = document.createElement('div');
    div.className = 'err';
    div.innerHTML = `
      <div class="t">${e.task ? '[' + escapeHtml(e.task) + '] ' : ''}${new Date(e.ts).toLocaleTimeString()}</div>
      <div class="m">${escapeHtml(e.message || '')}</div>
    `;
    frag.appendChild(div);
  }
  els.errors.innerHTML = '';
  els.errors.appendChild(frag);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderLog() {
  const frag = document.createDocumentFragment();
  const q = state.query;
  for (const e of logItems) {
    if (!state.filters.has(e.level)) continue;
    if (q && !(`${e.message} ${e.task || ''} ${e.status || ''}`.toLowerCase().includes(q))) continue;

    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `
      <span class="ts">${fmtTs(e.ts)}</span>
      <span class="lvl ${e.level}">${escapeHtml(e.level)}</span>
      ${e.task ? `<span class="task">[${escapeHtml(e.task)}]</span>` : ''}
      <span class="msg">${escapeHtml(e.message || '')}</span>
      ${e.progress != null ? `<span class="progress">${Math.round(e.progress * 100)}%</span>` : ''}
    `;
    frag.appendChild(row);
  }
  els.log.innerHTML = '';
  els.log.appendChild(frag);
  els.log.scrollTop = els.log.scrollHeight;
}

async function init() {
  try {
    const [s, ev] = await Promise.all([
      fetch('/api/summary').then(r => r.json()),
      fetch('/api/events?limit=200').then(r => r.json())
    ]);
    for (const e of ev.items || []) logItems.push(e);
    renderLog();
    renderSummary(s);
  } catch (e) {
    console.error('Failed to initialize:', e);
  }

  connectSSE();
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  setConn(false);
  es.addEventListener('open', () => setConn(true));
  es.addEventListener('error', () => setConn(false));
  es.addEventListener('summary', (ev) => {
    try { renderSummary(JSON.parse(ev.data)); } catch {}
  });
  es.addEventListener('log', (ev) => {
    try {
      const e = JSON.parse(ev.data);
      logItems.push(e);
      if (logItems.length > 1000) logItems.shift();
      if (e.task) {
        const t = taskState[e.task] || { lastStatus: null, progress: null, count: 0, lastAt: null };
        t.count += 1;
        t.lastStatus = e.status || t.lastStatus;
        if (typeof e.progress === 'number') t.progress = e.progress;
        t.lastAt = e.ts;
        taskState[e.task] = t;
        renderTasks();
      }
      renderLog();
    } catch {}
  });
}

function setConn(on) {
  els.connDot.style.background = on ? 'var(--ok)' : 'var(--err)';
}

init();
