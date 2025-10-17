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
  planTree: document.getElementById('planTree')
};

const state = {
  filters: new Set(['info', 'warn', 'error']),
  query: '',
  componentPlans: {},
  selectedPlan: null,
  expandedNodes: new Set(),
  lastPlanSelected: null
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
  if (!els.planCard || !els.planSelect || !els.planMeta || !els.planTree) {
    return;
  }
  const slugs = Object.keys(state.componentPlans || {}).sort();
  if (!slugs.length) {
    els.planCard.style.display = 'none';
    return;
  }
  els.planCard.style.display = '';

  const select = els.planSelect;
  const current = select ? select.value : null;
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
  } else if (current && slugs.includes(current)) {
    state.selectedPlan = current;
    if (select) select.value = current;
  } else {
    state.selectedPlan = slugs[0];
    if (select) select.value = state.selectedPlan;
  }

  if (state.selectedPlan !== state.lastPlanSelected) {
    state.expandedNodes = new Set();
    state.lastPlanSelected = state.selectedPlan;
  }

  const plan = state.componentPlans[state.selectedPlan];
  if (!plan) {
    els.planMeta.textContent = 'Planner data unavailable yet.';
    els.planTree.innerHTML = '';
    return;
  }

  const updated = plan.generated_at || plan.generatedAt || plan.generatedAtUtc;
  const status = plan.status || 'in_progress';
  const label = status === 'completed' ? 'Plan generated' : 'Planning in progress';
  const timestamp = updated ? new Date(updated).toLocaleString() : '—';
  els.planMeta.textContent = `${label} · Card: ${plan.card_path || 'unknown'} · Updated ${timestamp}`;

  const tree = document.createDocumentFragment();
  const components = Array.isArray(plan.components) ? plan.components : [];
  if (!components.length) {
    const empty = document.createElement('div');
    empty.className = 'tree-node level-0';
    empty.textContent = 'No components mapped yet.';
    tree.appendChild(empty);
  } else {
    components.forEach((component, index) => {
      appendComponent(tree, component, index, state.selectedPlan);
    });
  }

  els.planTree.innerHTML = '';
  els.planTree.appendChild(tree);
}

function appendComponent(target, component, index, slug) {
  const id = normalizeId(slug, component && component.id ? component.id : `component-${index}`);
  const children = component && Array.isArray(component.subcomponents) ? component.subcomponents : [];
  const { node, expanded } = buildTreeNode({
    id,
    level: 0,
    title: component && component.name ? component.name : 'Component',
    summary: component && component.summary ? component.summary : null,
    meta: filterStrings([
      component && component.rationale,
      component && component.notes
    ]),
    badges: [],
    hasChildren: children.length > 0
  });
  target.appendChild(node);
  if (expanded) {
    children.forEach((sub, subIdx) => appendSubcomponent(target, sub, subIdx, slug, id));
  }
}

function appendSubcomponent(target, subcomponent, index, slug, parentId) {
  const id = normalizeId(slug, subcomponent && subcomponent.id ? subcomponent.id : `${parentId}-sub-${index}`);
  const tests = subcomponent && Array.isArray(subcomponent.tests) ? subcomponent.tests : [];
  const metaLines = filterStrings([
    subcomponent && subcomponent.summary,
    Array.isArray(subcomponent && subcomponent.dependencies) && subcomponent.dependencies.length
      ? `Deps: ${subcomponent.dependencies.join(', ')}`
      : null,
    Array.isArray(subcomponent && subcomponent.risks) && subcomponent.risks.length
      ? `Risks: ${subcomponent.risks.join(', ')}`
      : null
  ]);
  const { node, expanded } = buildTreeNode({
    id,
    level: 1,
    title: subcomponent && subcomponent.name ? subcomponent.name : 'Subcomponent',
    summary: null,
    meta: metaLines,
    badges: [],
    hasChildren: tests.length > 0
  });
  target.appendChild(node);
  if (expanded) {
    tests.forEach((test, testIdx) => appendTest(target, test, testIdx, slug, id));
  }
}

function appendTest(target, test, index, slug, parentId) {
  const id = normalizeId(slug, test && test.id ? test.id : `${parentId}-test-${index}`);
  const tags = Array.isArray(test && test.tags) ? test.tags : [];
  const questionTitle = normaliseQuestion(test);
  const measurement = extractMeasurement(test);
  const contextLines = [];
  const context = extractContext(test);
  if (context) {
    contextLines.push(`Context: ${context}`);
  }
  const meta = filterStrings(contextLines);
  const badges = [];
  if (test && typeof test.status === 'string' && test.status.trim()) {
    badges.push({ label: test.status, variant: `status-${test.status.trim().toLowerCase()}` });
  }
  tags.forEach((tag) => badges.push({ label: tag, variant: 'tag' }));

  const { node } = buildTreeNode({
    id,
    level: 2,
    title: questionTitle,
    summary: measurement ? `Measurement: ${measurement}` : null,
    meta,
    badges,
    hasChildren: false
  });
  target.appendChild(node);
}

function buildTreeNode({ id, level, title, summary, meta, badges, hasChildren }) {
  let expanded = hasChildren ? state.expandedNodes.has(id) : true;
  if (hasChildren && !state.expandedNodes.has(id) && level < 2) {
    state.expandedNodes.add(id);
    expanded = true;
  }
  const node = document.createElement('div');
  node.className = `tree-node level-${level}`;

  const header = document.createElement('div');
  header.className = 'tree-node-header';

  const toggle = document.createElement('button');
  toggle.className = 'tree-toggle' + (hasChildren ? '' : ' leaf');
  toggle.textContent = hasChildren ? (expanded ? '▼' : '▶') : '•';
  if (hasChildren) {
    const toggleExpand = (event) => {
      event.stopPropagation();
      if (state.expandedNodes.has(id)) state.expandedNodes.delete(id);
      else state.expandedNodes.add(id);
      renderPlanner();
    };
    toggle.addEventListener('click', toggleExpand);
    header.addEventListener('click', (event) => {
      if (event.target !== toggle) toggleExpand(event);
    });
  }
  header.appendChild(toggle);

  const titleEl = document.createElement('div');
  titleEl.className = `tree-title level-${level}`;
  titleEl.textContent = title || 'Untitled';
  header.appendChild(titleEl);

  node.appendChild(header);

  if (summary) {
    const summaryEl = document.createElement('div');
    summaryEl.className = 'tree-summary';
    summaryEl.textContent = summary;
    node.appendChild(summaryEl);
  }
  if (Array.isArray(meta)) {
    meta.filter(Boolean).forEach((line) => {
      const metaEl = document.createElement('div');
      metaEl.className = 'tree-meta';
      metaEl.textContent = line;
      node.appendChild(metaEl);
    });
  }
  if (Array.isArray(badges) && badges.length) {
    const badgeWrap = document.createElement('div');
    badgeWrap.className = 'tree-badges';
    badges.forEach((badge) => {
      const { label, variant } = normalizeBadge(badge);
      const span = document.createElement('span');
      span.className = 'tree-badge' + (variant ? ` ${variant}` : '');
      span.textContent = label;
      badgeWrap.appendChild(span);
    });
    node.appendChild(badgeWrap);
  }

  return { node, expanded };
}

function normalizeId(slug, id) {
  return `${slug || 'plan'}::${id}`;
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

function normalizeBadge(badge) {
  if (!badge) return { label: '', variant: '' };
  if (typeof badge === 'string') {
    return { label: badge, variant: '' };
  }
  if (typeof badge === 'object') {
    const label = badge.label || badge.value || '';
    return {
      label: String(label),
      variant: badge.variant ? String(badge.variant) : ''
    };
  }
  return { label: String(badge), variant: '' };
}

function filterStrings(values) {
  if (!Array.isArray(values)) return [];
  return values.filter(Boolean);
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
