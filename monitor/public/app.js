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
  planFeature: document.getElementById('planFeature'),
  planStatus: document.getElementById('planStatus'),
  planProgressTrack: document.getElementById('planProgressTrack'),
  planProgressBar: document.getElementById('planProgressBar'),
  planProgressLabel: document.getElementById('planProgressLabel'),
  planInfo: document.getElementById('planInfo'),
  planTree: document.getElementById('planTree')
};

const state = {
  filters: new Set(['info', 'warn', 'error']),
  query: '',
  componentPlans: {},
  selectedPlan: null
};

const PASS_STATUSES = new Set(['pass', 'passed', 'complete', 'completed', 'success', 'succeeded', 'done']);
const FAIL_STATUSES = new Set(['fail', 'failed', 'error', 'broken']);

updatePlanTopbar(null, []);

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

if (els.planSelect) {
  els.planSelect.addEventListener('change', () => {
    const value = els.planSelect.value;
    state.selectedPlan = value || null;
    renderPlanner();
  });
}

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

  if (els.planCard) {
    state.componentPlans = s.componentPlans || {};
    if (!state.selectedPlan || !(state.selectedPlan in state.componentPlans)) {
      const slugs = Object.keys(state.componentPlans);
      state.selectedPlan = slugs.length ? slugs[0] : null;
    }
    renderPlanner();
  }

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
  if (!els.planCard || !els.planSelect || !els.planTree) {
    return;
  }
  const slugs = Object.keys(state.componentPlans || {}).sort();
  if (!slugs.length) {
    els.planCard.style.display = 'none';
    return;
  }
  els.planCard.style.display = '';

  const select = els.planSelect;
  const previous = select ? select.value : null;
  if (select) {
    select.innerHTML = '';
    for (const slug of slugs) {
      const opt = document.createElement('option');
      opt.value = slug;
      opt.textContent = slug;
      select.appendChild(opt);
    }
  }
  if (state.selectedPlan && slugs.includes(state.selectedPlan)) {
    if (select) select.value = state.selectedPlan;
  } else if (previous && slugs.includes(previous)) {
    state.selectedPlan = previous;
    if (select) select.value = previous;
  } else {
    state.selectedPlan = slugs[0];
    if (select) select.value = state.selectedPlan;
  }

  const plan = state.componentPlans[state.selectedPlan];
  if (!plan) {
    els.planTree.innerHTML = '';
    updatePlanTopbar(null, []);
    return;
  }

  const rows = collectPlanRows(plan);
  updatePlanTopbar(plan, rows);
  const table = buildPlanTable(rows);
  els.planTree.innerHTML = '';
  els.planTree.appendChild(table);
}

function normaliseQuestion(test) {
  const candidates = [
    test && typeof test.question === 'string' ? test.question : null,
    test && typeof test.name === 'string' ? test.name : null,
    test && typeof test.title === 'string' ? test.title : null
  ].filter(Boolean);
  const base = candidates.length ? candidates[0] : 'Unknown test';
  return ensureQuestion(base);
}

function ensureQuestion(text) {
  if (!text) return 'Unknown test?';
  let cleaned = text.trim();
  if (!cleaned) return 'Unknown test?';
  if (/[?！？]$/.test(cleaned)) return cleaned;
  cleaned = cleaned.replace(/[.!;]+$/u, '').trim();
  const lowered = cleaned.toLowerCase();
  const prefixes = ['does ', 'is ', 'can ', 'will ', 'should ', 'did ', 'are '];
  if (prefixes.some((p) => lowered.startsWith(p))) {
    return `${cleaned}?`;
  }
  const first = cleaned.charAt(0).toUpperCase();
  return `Does ${first}${cleaned.slice(1)}?`;
}

function extractMeasurement(test) {
  if (!test) return '';
  if (typeof test.measurement === 'string' && test.measurement.trim()) {
    return test.measurement.trim();
  }
  if (typeof test.verification === 'string' && test.verification.trim()) {
    return test.verification.trim();
  }
  if (typeof test.description === 'string' && test.description.trim()) {
    return test.description.trim();
  }
  return '';
}

function extractContext(test) {
  if (!test) return '';
  if (typeof test.context === 'string' && test.context.trim()) {
    return test.context.trim();
  }
  const desc = typeof test.description === 'string' ? test.description.trim() : '';
  const measurement = extractMeasurement(test);
  if (desc && measurement && measurement.includes(desc)) {
    return '';
  }
  return desc;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function collectPlanRows(plan) {
  const rows = [];
  const components = Array.isArray(plan && plan.components) ? plan.components : [];
  components.forEach((component, index) => {
    const componentName = component && component.name ? component.name : `Component ${index + 1}`;
    const scope = {
      component: componentName,
      componentTooltip: buildScopeTooltip(component),
      componentRisks: dedupeStrings(component && Array.isArray(component.risks) ? component.risks : [])
    };
    rows.push(...collectRowsFromNode(component, scope));
  });
  return rows;
}

function collectRowsFromNode(node, scope) {
  const rows = [];
  if (!node || typeof node !== 'object') {
    return rows;
  }
  const tests = normalizeTests(node.tests, scope.subcomponent || scope.component);
  tests.forEach((test) => {
    const risks = dedupeStrings([
      ...(scope.componentRisks || []),
      ...(scope.subcomponentRisks || []),
      ...(test.risks || [])
    ]);
    rows.push({
      component: scope.component,
      componentTooltip: scope.componentTooltip || '',
      subcomponent: scope.subcomponent || '',
      subcomponentTooltip: scope.subcomponentTooltip || '',
      test,
      risks
    });
  });
  const subs = Array.isArray(node.subcomponents) ? node.subcomponents : [];
  subs.forEach((sub, index) => {
    const subName = sub && sub.name ? sub.name : `Subcomponent ${index + 1}`;
    const subScope = {
      component: scope.component,
      componentTooltip: scope.componentTooltip,
      componentRisks: scope.componentRisks || [],
      subcomponent: subName,
      subcomponentTooltip: buildScopeTooltip(sub),
      subcomponentRisks: dedupeStrings(sub && Array.isArray(sub.risks) ? sub.risks : [])
    };
    rows.push(...collectRowsFromNode(sub, subScope));
  });
  return rows;
}

function buildScopeTooltip(node) {
  if (!node || typeof node !== 'object') return '';
  const parts = [];
  ['summary', 'rationale', 'notes'].forEach((field) => {
    const value = node[field];
    if (typeof value === 'string' && value.trim()) {
      parts.push(value.trim());
    }
  });
  return parts.join('\n\n');
}

function normalizeTests(tests, sourceName) {
  if (!Array.isArray(tests) || !tests.length) return [];
  const seen = new Set();
  const items = [];
  tests.forEach((test, index) => {
    if (!test) return;
    const identifier =
      (test.id ||
        test.question ||
        test.name ||
        test.title ||
        `test-${index}`) +
      `::${sourceName || 'component'}`;
    const key = identifier.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    items.push({
      title: normaliseQuestion(test),
      measurement: extractMeasurement(test),
      context: extractContext(test),
      status: typeof test.status === 'string' ? test.status.toLowerCase() : 'proposed',
      tags: Array.isArray(test.tags) ? test.tags : [],
      source: sourceName || '',
      risks: dedupeStrings(test && Array.isArray(test.risks) ? test.risks : []),
      raw: test
    });
  });
  return items;
}

function summariseRows(rows) {
  let pass = 0;
  let fail = 0;
  let todo = 0;
  rows.forEach((row) => {
    const status = (row.test.status || '').toLowerCase();
    if (PASS_STATUSES.has(status)) pass++;
    else if (FAIL_STATUSES.has(status)) fail++;
    else todo++;
  });
  return { pass, fail, todo, total: rows.length };
}

function updatePlanTopbar(plan, rows) {
  if (!els.planFeature || !els.planStatus || !els.planProgressBar || !els.planProgressLabel || !els.planInfo) {
    return;
  }

  const slug = state.selectedPlan || '';
  const humanSlug = slug ? slug.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : '—';
  els.planFeature.textContent = humanSlug;

  const statusEl = els.planStatus;
  statusEl.className = 'plan-status';
  let statusClass = 'plan-status-in_progress';
  let statusLabel = 'IN PROGRESS';
  if (!plan) {
    statusClass = 'plan-status-unknown';
    statusLabel = 'UNKNOWN';
  } else {
    const status = (plan.status || 'in_progress').toLowerCase();
    if (status === 'completed') {
      statusClass = 'plan-status-completed';
      statusLabel = 'COMPLETED';
    } else if (status === 'failed') {
      statusClass = 'plan-status-failed';
      statusLabel = 'FAILED';
    }
  }
  statusEl.classList.add(statusClass);
  statusEl.textContent = statusLabel;

  const summary = summariseRows(rows);
  const pass = summary.pass;
  const fail = summary.fail;
  const total = summary.total;
  const progress = total ? Math.round((pass / total) * 100) : 0;
  if (els.planProgressBar) {
    const width = Math.max(0, Math.min(progress, 100));
    els.planProgressBar.style.width = `${width}%`;
  }
  if (els.planProgressTrack) {
    els.planProgressTrack.setAttribute('aria-valuenow', String(progress));
    const progressLabel = total
      ? `Test progress: ${pass} passing, ${fail} failing, ${summary.todo} pending`
      : 'Test progress unavailable';
    els.planProgressTrack.setAttribute('aria-label', progressLabel);
  }
  els.planProgressLabel.textContent = total
    ? `${pass}/${total} Passing · ${fail} Failing`
    : '0/0 Passing · 0 Failing';

  if (els.planInfo) {
    if (!plan) {
      els.planInfo.disabled = true;
      els.planInfo.title = 'Planner data unavailable.';
    } else {
      const updated = plan.generated_at || plan.generatedAt || plan.generatedAtUtc;
      const timestamp = updated ? new Date(updated).toLocaleString() : '—';
      const parts = [];
      if (plan.card_path) parts.push(`Card: ${plan.card_path}`);
      parts.push(`Generated: ${timestamp}`);
      parts.push(`Tests · Pass: ${pass} · Fail: ${fail} · Pending: ${summary.todo}`);
      els.planInfo.disabled = false;
      els.planInfo.title = parts.join('\n');
    }
  }
}

function buildPlanTable(rows) {
  const table = document.createElement('table');
  table.className = 'plan-table plan-table-compact';
  table.innerHTML = `
    <thead>
      <tr>
        <th>Component</th>
        <th>Subcomponent</th>
        <th>Test Case</th>
        <th>Status</th>
        <th>Implementation &amp; Notes</th>
        <th>Risks</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement('tbody');

  if (!rows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.className = 'muted';
    cell.textContent = 'No planner data yet.';
    row.appendChild(cell);
    tbody.appendChild(row);
  } else {
    let lastGroupKey = null;
    let lastComponentShown = null;
    let isAlternateStrip = false;

    rows.forEach((rowData, index) => {
      const groupKey = `${rowData.component || ''}::${rowData.subcomponent || ''}`;
      const isNewGroup = groupKey !== lastGroupKey;
      if (isNewGroup) {
        if (lastGroupKey !== null) {
          isAlternateStrip = !isAlternateStrip;
        }
        lastGroupKey = groupKey;
      }

      const row = document.createElement('tr');
      row.className = 'plan-row';
      if (isNewGroup) row.classList.add('group-start');
      if (isAlternateStrip) row.classList.add('group-alt');
      const nextRow = rows[index + 1];
      const nextKey = nextRow ? `${nextRow.component || ''}::${nextRow.subcomponent || ''}` : null;
      if (!nextRow || nextKey !== groupKey) {
        row.classList.add('group-end');
      }

      const componentCell = document.createElement('td');
      componentCell.className = 'scope scope-component';
      if (isNewGroup || rowData.component !== lastComponentShown) {
        componentCell.textContent = rowData.component || '—';
        if (rowData.componentTooltip) componentCell.title = rowData.componentTooltip;
        lastComponentShown = rowData.component;
      } else {
        componentCell.textContent = '';
        if (rowData.component) componentCell.setAttribute('aria-label', rowData.component);
        componentCell.classList.add('scope-repeat');
      }
      row.appendChild(componentCell);

      const subCell = document.createElement('td');
      subCell.className = 'scope scope-sub';
      if (rowData.subcomponent) {
        if (isNewGroup) {
          subCell.textContent = rowData.subcomponent;
          if (rowData.subcomponentTooltip) subCell.title = rowData.subcomponentTooltip;
        } else {
          subCell.textContent = '';
          subCell.setAttribute('aria-label', rowData.subcomponent);
          subCell.classList.add('scope-repeat');
        }
      } else {
        subCell.textContent = '—';
        subCell.classList.add('scope-empty');
      }
      row.appendChild(subCell);

      const testCell = document.createElement('td');
      const strong = document.createElement('strong');
      strong.textContent = rowData.test.title || 'Test case';
      testCell.appendChild(strong);
      if (rowData.test.context) {
        const context = document.createElement('div');
        context.className = 'meta meta-context';
        context.textContent = rowData.test.context;
        testCell.appendChild(context);
      }
      if (rowData.test.tags && rowData.test.tags.length) {
        const tags = document.createElement('div');
        tags.className = 'meta meta-tags';
        tags.textContent = rowData.test.tags.map((tag) => `#${tag}`).join(' ');
        testCell.appendChild(tags);
      }
      row.appendChild(testCell);

      const statusCell = document.createElement('td');
      statusCell.appendChild(buildStatusPill(rowData.test.status));
      row.appendChild(statusCell);

      const implCell = document.createElement('td');
      implCell.className = 'impl';
      if (rowData.test.measurement) {
        implCell.innerHTML = formatRichText(rowData.test.measurement);
      } else {
        implCell.textContent = 'No measurement provided.';
      }
      row.appendChild(implCell);

      const riskCell = document.createElement('td');
      riskCell.className = 'risks';
      if (rowData.risks.length) {
        riskCell.classList.add('risk-alert');
        rowData.risks.forEach((risk) => {
          const line = document.createElement('div');
          line.className = 'risk-line';
          line.textContent = risk;
          riskCell.appendChild(line);
        });
      } else {
        riskCell.classList.add('risk-none');
        riskCell.setAttribute('aria-label', 'No documented risks');
      }
      row.appendChild(riskCell);

      tbody.appendChild(row);
    });
  }

  table.appendChild(tbody);
  return table;
}

function buildStatusPill(status) {
  const pill = document.createElement('span');
  const normalized = (status || '').toLowerCase();
  let label = (status || 'proposed').toUpperCase();
  let klass = 'status-proposed';
  if (['pass', 'passed', 'completed', 'done', 'success'].includes(normalized)) {
    klass = 'status-pass';
    label = 'PASS';
  } else if (['fail', 'failed', 'error', 'broken'].includes(normalized)) {
    klass = 'status-fail';
    label = 'FAIL';
  }
  pill.className = `status-pill ${klass}`;
  pill.textContent = label;
  return pill;
}

function formatRichText(text) {
  if (!text) return '';
  return text
    .split('`')
    .map((segment, idx) => {
      const escaped = escapeHtml(segment);
      return idx % 2 === 1 ? `<code>${escaped}</code>` : escaped;
    })
    .join('')
    .replace(/\n/g, '<br>');
}

function dedupeStrings(values) {
  if (!Array.isArray(values)) return [];
  const seen = new Set();
  const out = [];
  values.forEach((value) => {
    const val = typeof value === 'string' ? value.trim() : '';
    if (!val || seen.has(val)) return;
    seen.add(val);
    out.push(val);
  });
  return out;
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
