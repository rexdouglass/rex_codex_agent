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
  searchBox: document.getElementById('searchBox'),
  planCard: document.getElementById('planCard'),
  planSelect: document.getElementById('planSelect'),
  planMeta: document.getElementById('planMeta'),
  planGrid: document.getElementById('planGrid')
};

const state = {
  filters: new Set(['info', 'warn', 'error']),
  query: '',
  componentPlans: {},
  selectedPlan: null
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

els.planSelect.addEventListener('change', () => {
  const value = els.planSelect.value;
  state.selectedPlan = value || null;
  renderPlanner();
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

  state.componentPlans = s.componentPlans || {};
  if (!state.selectedPlan || !(state.selectedPlan in state.componentPlans)) {
    const slugs = Object.keys(state.componentPlans);
    state.selectedPlan = slugs.length ? slugs[0] : null;
  }
  renderPlanner();

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

function renderPlanner() {
  const slugs = Object.keys(state.componentPlans || {}).sort();
  if (!slugs.length) {
    els.planCard.style.display = 'none';
    return;
  }
  els.planCard.style.display = '';

  const select = els.planSelect;
  const current = select.value;
  select.innerHTML = '';
  for (const slug of slugs) {
    const opt = document.createElement('option');
    opt.value = slug;
    opt.textContent = slug;
    select.appendChild(opt);
  }
  if (state.selectedPlan && slugs.includes(state.selectedPlan)) {
    select.value = state.selectedPlan;
  } else if (current && slugs.includes(current)) {
    state.selectedPlan = current;
    select.value = current;
  } else {
    state.selectedPlan = slugs[0];
    select.value = state.selectedPlan;
  }

  const plan = state.componentPlans[state.selectedPlan];
  if (!plan) {
    els.planMeta.textContent = 'Planner data unavailable yet.';
    els.planGrid.innerHTML = '';
    return;
  }

  const updated = plan.generated_at || plan.generatedAt || plan.generatedAtUtc;
  const status = plan.status || 'in_progress';
  const label = status === 'completed' ? 'Plan generated' : 'Planning in progress';
  const timestamp = updated ? new Date(updated).toLocaleString() : '—';
  els.planMeta.textContent = `${label} · Card: ${plan.card_path || 'unknown'} · Updated ${timestamp}`;

  const grid = document.createDocumentFragment();
  const components = Array.isArray(plan.components) ? plan.components : [];
  if (!components.length) {
    const row = document.createElement('div');
    row.className = 'plan-row';
    row.innerHTML = '<div class="plan-col plan-empty">No components mapped yet.</div><div class="plan-col"></div><div class="plan-col"></div>';
    grid.appendChild(row);
  }

  for (const comp of components) {
    const subcomponents = Array.isArray(comp.subcomponents) && comp.subcomponents.length ? comp.subcomponents : [null];
    subcomponents.forEach((sub, subIdx) => {
      const tests = sub && Array.isArray(sub.tests) && sub.tests.length ? sub.tests : [null];
      tests.forEach((test, testIdx) => {
        const row = document.createElement('div');
        row.className = 'plan-row';

        row.appendChild(buildComponentCell(comp, subIdx === 0 && testIdx === 0));
        row.appendChild(buildSubcomponentCell(sub, testIdx === 0));
        row.appendChild(buildTestCell(test));

        grid.appendChild(row);
      });
    });
  }

  els.planGrid.innerHTML = '';
  els.planGrid.appendChild(grid);
}

function buildComponentCell(component, showContent) {
  const div = document.createElement('div');
  div.className = 'plan-col';
  if (!showContent || !component) {
    div.classList.add('plan-empty');
    div.textContent = showContent ? '—' : '';
    return div;
  }
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = component.name || 'Component';
  div.appendChild(title);
  if (component.summary) {
    const summary = document.createElement('div');
    summary.className = 'summary';
    summary.textContent = component.summary;
    div.appendChild(summary);
  }
  if (component.rationale) {
    const rationale = document.createElement('div');
    rationale.className = 'summary';
    rationale.textContent = component.rationale;
    div.appendChild(rationale);
  }
  return div;
}

function buildSubcomponentCell(subcomponent, showContent) {
  const div = document.createElement('div');
  div.className = 'plan-col';
  if (!showContent) {
    div.classList.add('plan-empty');
    div.textContent = '';
    return div;
  }
  if (!subcomponent) {
    div.classList.add('plan-empty');
    div.textContent = 'No subcomponents defined yet.';
    return div;
  }
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = subcomponent.name || 'Subcomponent';
  div.appendChild(title);
  if (subcomponent.summary) {
    const summary = document.createElement('div');
    summary.className = 'summary';
    summary.textContent = subcomponent.summary;
    div.appendChild(summary);
  }
  const badges = [];
  if (Array.isArray(subcomponent.dependencies) && subcomponent.dependencies.length) {
    badges.push(`Deps: ${subcomponent.dependencies.join(', ')}`);
  }
  if (Array.isArray(subcomponent.risks) && subcomponent.risks.length) {
    badges.push(`Risks: ${subcomponent.risks.join(', ')}`);
  }
  if (badges.length) {
    const meta = document.createElement('div');
    meta.className = 'summary';
    meta.textContent = badges.join(' | ');
    div.appendChild(meta);
  }
  return div;
}

function buildTestCell(test) {
  const div = document.createElement('div');
  div.className = 'plan-col';
  if (!test) {
    div.classList.add('plan-empty');
    div.textContent = 'No proposed tests yet.';
    return div;
  }
  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = test.name || 'Test';
  if (test.status) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = test.status;
    title.appendChild(badge);
  }
  div.appendChild(title);
  if (test.description) {
    const desc = document.createElement('div');
    desc.className = 'summary';
    desc.textContent = test.description;
    div.appendChild(desc);
  }
  if (test.verification) {
    const verify = document.createElement('div');
    verify.className = 'summary';
    verify.textContent = `Verify: ${test.verification}`;
    div.appendChild(verify);
  }
  if (Array.isArray(test.tags) && test.tags.length) {
    const tags = document.createElement('div');
    tags.className = 'summary';
    tags.textContent = `Tags: ${test.tags.join(', ')}`;
    div.appendChild(tags);
  }
  return div;
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
      if (e.meta && e.meta.plan && e.meta.slug) {
        state.componentPlans[e.meta.slug] = e.meta.plan;
        if (!state.selectedPlan || !(state.selectedPlan in state.componentPlans)) {
          state.selectedPlan = e.meta.slug;
        }
        renderPlanner();
      }
      renderLog();
    } catch {}
  });
}

function setConn(on) {
  els.connDot.style.background = on ? 'var(--ok)' : 'var(--err)';
}

init();
