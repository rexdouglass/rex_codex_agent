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
  codingStrategies: {},
  selectedPlan: null
};

const PASS_STATUSES = new Set(['pass', 'passed', 'complete', 'completed', 'success', 'succeeded', 'done']);
const FAIL_STATUSES = new Set(['fail', 'failed', 'error', 'broken']);

updatePlanTopbar(null, [], {});

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

function formatDateTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return String(iso);
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
    state.codingStrategies = s.codingStrategies || {};
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
    updatePlanTopbar(null, [], {});
    return;
  }

  const strategies = getStrategyMap(state.selectedPlan);
  const rows = collectPlanRows(plan);
  updatePlanTopbar(plan, rows, strategies);
  const table = buildPlanTable(rows, strategies);
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

function extractTestId(test, fallback) {
  if (!test || typeof test !== 'object') return fallback;
  const candidates = [
    test.id,
    test.test_id,
    test.testId,
    test.test,
    test.test_case,
    test.name,
    test.slug
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }
  return fallback;
}

function getStrategyMap(slug) {
  if (!slug) return {};
  const bucket = state.codingStrategies[slug];
  if (!bucket) return {};
  if (bucket.tests && typeof bucket.tests === 'object') return bucket.tests;
  return bucket;
}

function resolveStatus(testStatus, strategyStatus) {
  const candidate = (strategyStatus || testStatus || '').toLowerCase();
  return candidate || 'proposed';
}

function normalizeStrategyKey(value) {
  if (!value) return '';
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function ensureStrategyBucket(slug) {
  state.codingStrategies[slug] = state.codingStrategies[slug] || { tests: {} };
  const bucket = state.codingStrategies[slug];
  if (!bucket.tests) bucket.tests = {};
  return bucket.tests;
}

function appendStepEntry(entry, text) {
  if (!text) return;
  const trimmed = String(text).trim();
  if (!trimmed) return;
  entry.strategy = entry.strategy || [];
  const key = normalizeStrategyKey(trimmed);
  const existingKeys = new Set(entry.strategy.map((step) => normalizeStrategyKey(step)));
  if (!existingKeys.has(key)) {
    entry.strategy.push(trimmed);
  }
}

function mergeFilesEntry(entry, files) {
  if (!files || !files.length) return;
  entry.files = entry.files || [];
  const merged = new Set(entry.files);
  files.forEach((file) => {
    if (file) merged.add(String(file));
  });
  entry.files = Array.from(merged);
}

function findMatchingTestKeyClient(tests, candidate) {
  if (!candidate) return null;
  if (candidate in tests) return candidate;
  const normalizedCandidate = normalizeStrategyKey(candidate);
  for (const key of Object.keys(tests)) {
    const entry = tests[key] || {};
    const normalized = entry.normalized || normalizeStrategyKey(key);
    if (normalized && normalized === normalizedCandidate) {
      return key;
    }
  }
  return null;
}

function applyStrategyUpdateClient(slug, testIds, ts, updater) {
  const tests = ensureStrategyBucket(slug);
  const ids = testIds && testIds.length ? testIds : Object.keys(tests);
  if (!ids.length) return;
  ids.forEach((candidate) => {
    const matchKey = findMatchingTestKeyClient(tests, candidate);
    const key = matchKey || candidate;
    const entry = tests[key] || { strategy: [], files: [] };
    entry.normalized = entry.normalized || normalizeStrategyKey(key);
    updater(entry, key);
    entry.lastUpdated = ts;
    tests[key] = entry;
  });
}

function findStrategyEntry(strategies, testId) {
  if (!strategies || !testId) return null;
  if (strategies[testId]) return strategies[testId];
  const targetKey = normalizeStrategyKey(testId);
  for (const key of Object.keys(strategies)) {
    const entry = strategies[key];
    const normalized = entry && entry.normalized ? entry.normalized : normalizeStrategyKey(key);
    if (normalized && normalized === targetKey) {
      return entry;
    }
  }
  return null;
}

function normalizeStrategySteps(value) {
  const result = [];
  const seen = new Set();
  const push = (text) => {
    if (!text) return;
    const trimmed = String(text).trim();
    if (!trimmed) return;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(trimmed);
  };
  const walk = (input) => {
    if (!input) return;
    if (Array.isArray(input)) {
      input.forEach(walk);
      return;
    }
    if (typeof input === 'string') {
      input
        .split(/\r?\n+/)
        .map((line) => line.trim())
        .filter(Boolean)
        .forEach(push);
      return;
    }
    if (typeof input === 'object') {
      if (Array.isArray(input.steps)) {
        walk(input.steps);
        return;
      }
      ['summary', 'plan', 'text', 'description', 'notes'].forEach((key) => {
        if (key in input) walk(input[key]);
      });
    }
  };
  walk(value);
  return result;
}

function normalizeFileList(value) {
  const result = [];
  const seen = new Set();
  const push = (text) => {
    if (!text) return;
    const trimmed = String(text).trim();
    if (!trimmed) return;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(trimmed);
  };
  const walk = (input) => {
    if (!input) return;
    if (Array.isArray(input)) {
      input.forEach(walk);
      return;
    }
    if (typeof input === 'string') {
      input
        .split(/[\s,]+/)
        .map((token) => token.trim())
        .filter(Boolean)
        .forEach(push);
      return;
    }
    if (typeof input === 'object') {
      ['files', 'file_paths', 'paths', 'targets', 'touched_files'].forEach((key) => {
        if (key in input) walk(input[key]);
      });
    }
  };
  walk(value);
  return result;
}

function extractStrategyEntryFromRaw(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const testId = extractTestId(raw, '');
  if (!testId) return null;
  const strategy =
    normalizeStrategySteps(raw.strategy) ||
    normalizeStrategySteps(raw.strategies) ||
    normalizeStrategySteps(raw.steps) ||
    normalizeStrategySteps(raw.plan);
  const files = normalizeFileList(raw);
  const status = typeof raw.status === 'string' ? raw.status : undefined;
  const notes =
    typeof raw.notes === 'string'
      ? raw.notes
      : typeof raw.reason === 'string'
        ? raw.reason
        : undefined;
  const source = typeof raw.source === 'string' ? raw.source : undefined;
  return { id: testId, strategy, files, status, notes, source };
}

function extractCodingEntriesFromPlan(plan) {
  const entries = [];
  if (!plan || typeof plan !== 'object') return entries;
  const components = Array.isArray(plan.components) ? plan.components : [];
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    const tests = Array.isArray(node.tests) ? node.tests : [];
    tests.forEach((test) => {
      const entry = extractStrategyEntryFromRaw(test);
      if (entry) entries.push(entry);
    });
    const subs = Array.isArray(node.subcomponents) ? node.subcomponents : [];
    subs.forEach(visit);
  };
  components.forEach(visit);
  return entries;
}

function extractCodingEntries(meta) {
  const entries = [];
  if (!meta || typeof meta !== 'object') return entries;
  const direct = extractStrategyEntryFromRaw(meta);
  if (direct) entries.push(direct);
  ['strategy', 'strategies', 'entries', 'updates', 'tests'].forEach((key) => {
    const value = meta[key];
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach((item) => {
        const entry = extractStrategyEntryFromRaw(item);
        if (entry) entries.push(entry);
      });
    } else if (typeof value === 'object') {
      const entry = extractStrategyEntryFromRaw(value);
      if (entry) entries.push(entry);
    }
  });
  if (meta.plan) {
    let plan = meta.plan;
    if (typeof plan === 'string') {
      try {
        plan = JSON.parse(plan);
      } catch {
        plan = null;
      }
    }
    if (plan) {
      entries.push(...extractCodingEntriesFromPlan(plan));
    }
  }
  return entries;
}

function mergeCodingMeta(meta, ts) {
  if (!meta || meta.phase !== 'discriminator') return false;
  const slug = meta.slug;
  if (!slug) return false;
  const tests = ensureStrategyBucket(slug);
  let changed = false;
  const entries = extractCodingEntries(meta);
  entries.forEach((entry) => {
    if (!entry || !entry.id) return;
    const key = entry.id;
    const existing = tests[key] || { strategy: [], files: [] };
    if (entry.strategy && entry.strategy.length) {
      existing.strategy = entry.strategy;
    }
    if (entry.files && entry.files.length) {
      mergeFilesEntry(existing, entry.files);
    }
    if (entry.status) existing.status = entry.status;
    if (entry.notes) existing.notes = entry.notes;
    if (entry.source) existing.source = entry.source;
    existing.lastUpdated = ts;
    tests[key] = existing;
    changed = true;
  });

  const type = meta.type;
  if (!type) return changed;

  if (type === 'mechanical_fixes' && meta.changed) {
    const tools = Array.isArray(meta.tools) ? meta.tools.join(', ') : 'style tools';
    const targets = Array.isArray(meta.files) ? meta.files : meta.targets;
    applyStrategyUpdateClient(slug, null, ts, (entry) => {
      appendStepEntry(entry, `Applied mechanical fixes (${tools})`);
      mergeFilesEntry(entry, targets);
      entry.status = entry.status || 'in_progress';
    });
    changed = true;
  } else if (type === 'llm_patch_decision' && meta.accepted) {
    const files = Array.isArray(meta.files) ? meta.files : [];
    applyStrategyUpdateClient(slug, null, ts, (entry) => {
      appendStepEntry(entry, `Committed discriminator patch (${meta.reason || 'update'})`);
      mergeFilesEntry(entry, files);
      if (entry.status === 'failed') entry.status = 'in_progress';
    });
    changed = true;
  } else if (type === 'stage_end') {
    const command = meta.command || '';
    const isPytestStage = typeof command === 'string' && command.includes('pytest');
    if (isPytestStage) {
      if (meta.ok === false) {
        const failed = Array.isArray(meta.failed_tests) ? meta.failed_tests : [];
        const reason = meta.failure_reason || `Stage ${meta.identifier || ''} failed`;
        if (failed.length) {
          failed.forEach((testId) => {
            applyStrategyUpdateClient(slug, [testId], ts, (entry) => {
              appendStepEntry(entry, reason);
              mergeFilesEntry(entry, meta.failed_files);
              entry.status = 'failed';
            });
          });
        } else {
          applyStrategyUpdateClient(slug, null, ts, (entry) => {
            appendStepEntry(entry, reason);
            if (entry.status !== 'pass') entry.status = 'failed';
          });
        }
        changed = true;
      } else if (meta.ok) {
        applyStrategyUpdateClient(slug, null, ts, (entry) => {
          entry.status = 'pass';
        });
        changed = true;
      }
    }
  } else if (type === 'run_completed' && meta.ok) {
    applyStrategyUpdateClient(slug, null, ts, (entry) => {
      entry.status = 'pass';
    });
    changed = true;
  }

  return changed;
}

function collectPlanRows(plan) {
  const rows = [];
  const components = Array.isArray(plan && plan.components) ? plan.components : [];
  components.forEach((component, index) => {
    const componentName = component && component.name ? component.name : `Component ${index + 1}`;
    const scope = {
      component: componentName,
      componentTooltip: buildScopeTooltip(component)
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
    rows.push({
      component: scope.component,
      componentTooltip: scope.componentTooltip || '',
      subcomponent: scope.subcomponent || '',
      subcomponentTooltip: scope.subcomponentTooltip || '',
      test,
      testId: test.testId
    });
  });
  const subs = Array.isArray(node.subcomponents) ? node.subcomponents : [];
  subs.forEach((sub, index) => {
    const subName = sub && sub.name ? sub.name : `Subcomponent ${index + 1}`;
    const subScope = {
      component: scope.component,
      componentTooltip: scope.componentTooltip,
      subcomponent: subName,
      subcomponentTooltip: buildScopeTooltip(sub)
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
    const baseId = extractTestId(test, `test-${index}`);
    const identifier = `${baseId}::${sourceName || 'component'}`;
    const key = identifier.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    items.push({
      testId: baseId,
      title: normaliseQuestion(test),
      measurement: extractMeasurement(test),
      context: extractContext(test),
      status: typeof test.status === 'string' ? test.status.toLowerCase() : 'proposed',
      tags: Array.isArray(test.tags) ? test.tags : [],
      source: sourceName || '',
      raw: test
    });
  });
  return items;
}

function summariseRows(rows, strategies) {
  let pass = 0;
  let fail = 0;
  let todo = 0;
  rows.forEach((row) => {
    const entry = strategies && row.testId ? strategies[row.testId] : null;
    const status = resolveStatus(row.test.status, entry && entry.status);
    if (PASS_STATUSES.has(status)) pass++;
    else if (FAIL_STATUSES.has(status)) fail++;
    else todo++;
  });
  return { pass, fail, todo, total: rows.length };
}

function updatePlanTopbar(plan, rows, strategies) {
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

  const summary = summariseRows(rows, strategies || {});
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

function buildPlanTable(rows, strategies) {
  const table = document.createElement('table');
  table.className = 'plan-table plan-table-compact';
  table.innerHTML = `
    <thead>
      <tr>
        <th>Component</th>
        <th>Subcomponent</th>
        <th>Test Case</th>
        <th>Status</th>
        <th>Measurement</th>
        <th>Strategy (Discriminator)</th>
        <th>Target Files</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement('tbody');

  if (!rows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 7;
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

      const strategyEntry = findStrategyEntry(strategies, rowData.testId);
      const statusValue = resolveStatus(rowData.test.status, strategyEntry && strategyEntry.status);

      const statusCell = document.createElement('td');
      statusCell.appendChild(buildStatusPill(statusValue));
      row.appendChild(statusCell);

      const measureCell = document.createElement('td');
      measureCell.className = 'impl';
      if (rowData.test.measurement) {
        measureCell.innerHTML = formatRichText(rowData.test.measurement);
      } else {
        measureCell.textContent = 'No measurement provided.';
      }
      row.appendChild(measureCell);

      const strategyCell = document.createElement('td');
      strategyCell.className = 'strategy';
      if (strategyEntry && Array.isArray(strategyEntry.strategy) && strategyEntry.strategy.length) {
        strategyEntry.strategy.slice(0, 5).forEach((step) => {
          const line = document.createElement('div');
          line.className = 'strategy-step';
          line.textContent = step;
          strategyCell.appendChild(line);
        });
      } else {
        const span = document.createElement('span');
        span.className = 'muted';
        span.textContent = 'No strategy captured yet.';
        strategyCell.appendChild(span);
      }
      if (strategyEntry && strategyEntry.notes) {
        const note = document.createElement('div');
        note.className = 'notes';
        note.textContent = strategyEntry.notes;
        strategyCell.appendChild(note);
      }
      if (strategyEntry && strategyEntry.lastUpdated) {
        const meta = document.createElement('div');
        meta.className = 'notes strategy-meta';
        meta.textContent = `Updated ${formatDateTime(strategyEntry.lastUpdated)}`;
        strategyCell.appendChild(meta);
      }
      row.appendChild(strategyCell);

      const targetsCell = document.createElement('td');
      targetsCell.className = 'targets';
      if (strategyEntry && Array.isArray(strategyEntry.files) && strategyEntry.files.length) {
        strategyEntry.files.forEach((file) => {
          const code = document.createElement('code');
          code.textContent = file;
          targetsCell.appendChild(code);
        });
      } else {
        const span = document.createElement('span');
        span.className = 'muted';
        span.textContent = '—';
        targetsCell.appendChild(span);
      }
      row.appendChild(targetsCell);

      tbody.appendChild(row);
    });
  }

  table.appendChild(tbody);
  return table;
}

function buildStatusPill(status) {
  const pill = document.createElement('span');
  const normalized = (status || '').toLowerCase();
  let label = (status || 'proposed').replace(/[_-]+/g, ' ').toUpperCase();
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
      let plannerChanged = false;
      if (e.meta && e.meta.plan && e.meta.slug) {
        state.componentPlans[e.meta.slug] = e.meta.plan;
        if (!state.selectedPlan || !(state.selectedPlan in state.componentPlans)) {
          state.selectedPlan = e.meta.slug;
        }
        plannerChanged = true;
      }
      if (e.meta && mergeCodingMeta(e.meta, e.ts)) {
        plannerChanged = true;
      }
      if (plannerChanged) {
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
