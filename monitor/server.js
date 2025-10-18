/* Local passive monitor server
 * - Serves a UI at http://localhost:<port>
 * - Tails .agent/logs/events.jsonl (JSON lines) and plain text logs
 * - Streams updates via Server-Sent Events (SSE)
 * - Summarizes events (levels, tasks, errors, events/min)
 */
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const compression = require('compression');
const cors = require('cors');

const LOG_DIR = process.env.LOG_DIR || path.join(process.cwd(), '.agent', 'logs');
const EVENTS_FILE = process.env.EVENTS_FILE || path.join(LOG_DIR, 'events.jsonl');
const REPO_ROOT =
  process.env.REPO_ROOT
    ? path.resolve(process.env.REPO_ROOT)
    : path.resolve(LOG_DIR, '..', '..');
const STATIC_DIR = path.join(__dirname, 'public');
const COMPONENT_PLAN_DIR = path.join(REPO_ROOT, '.codex_ci');

const PORT_ENV = process.env.MONITOR_PORT || process.env.PORT || 4321;
const OPEN_BROWSER = (process.env.OPEN_BROWSER || 'false').toLowerCase() === 'true';
const PORT_RETRY_LIMIT = Number(process.env.MONITOR_PORT_RETRIES || '15');

ensureDirSync(LOG_DIR);
ensureDirSync(COMPONENT_PLAN_DIR);
ensureFileSync(EVENTS_FILE);

const app = express();
app.disable('x-powered-by');
app.use(compression());
app.use(cors());
app.use(express.static(STATIC_DIR, { fallthrough: true }));

// ====== In-memory state ======
const MAX_BUFFER = 1000;
const clients = new Set(); // SSE clients
const eventsBuffer = []; // ring buffer of last N events
const summary = {
  startedAt: new Date().toISOString(),
  lastEventAt: null,
  totals: { all: 0, info: 0, warn: 0, error: 0, debug: 0, task: 0, progress: 0 },
  recentEventsTimestamps: [], // for events/min calc (last 10 minutes)
  tasks: {}, // { [taskName]: { lastStatus, progress, count, lastAt } }
  lastErrors: [], // up to 20
  componentPlans: {}, // { [slug]: plan }
  codingStrategies: {} // { [slug]: { tests: { [testId]: entry } } }
};

// ====== Utilities ======
function ensureDirSync(p) {
  fs.mkdirSync(p, { recursive: true });
}
function ensureFileSync(p) {
  if (!fs.existsSync(p)) fs.writeFileSync(p, '');
}
function safeJSON(line) {
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}
function normalizeEvent(rawLine) {
  // Supports JSONL or plain text; ensure a normalized shape
  const json = safeJSON(rawLine);
  const ts = new Date().toISOString();
  if (json && typeof json === 'object') {
    const e = {
      ts: json.ts || json.timestamp || ts,
      level: (json.level || 'info').toLowerCase(),
      message: json.message || json.msg || '',
      task: json.task || json.step || undefined,
      status: json.status || undefined,
      progress: typeof json.progress === 'number' ? json.progress : undefined,
      meta: json.meta || json.data || undefined,
      type: json.type || undefined,
      slug: json.slug || undefined,
      phase: json.phase || undefined
    };
    // Bound progress
    if (typeof e.progress === 'number') {
      e.progress = Math.max(0, Math.min(1, e.progress));
    }
    return e;
  }
  // Fallback for plain text lines
  const trimmed = rawLine.trim();
  if (!trimmed) return null;
  return {
    ts,
    level: 'info',
    message: trimmed
  };
}

function updateSummary(e) {
  summary.lastEventAt = e.ts;
  summary.totals.all++;
  if (summary.totals[e.level] == null) summary.totals[e.level] = 0;
  summary.totals[e.level]++;

  // sliding timestamps
  const now = Date.now();
  summary.recentEventsTimestamps.push(now);
  const tenMinAgo = now - 10 * 60 * 1000;
  while (summary.recentEventsTimestamps.length && summary.recentEventsTimestamps[0] < tenMinAgo) {
    summary.recentEventsTimestamps.shift();
  }

  // tasks
  if (e.task) {
    const t = summary.tasks[e.task] || { lastStatus: null, progress: null, count: 0, lastAt: null };
    t.count += 1;
    t.lastStatus = e.status || t.lastStatus;
    if (typeof e.progress === 'number') t.progress = e.progress;
    t.lastAt = e.ts;
    summary.tasks[e.task] = t;
  }

  // errors
  if (e.level === 'error' || e.status === 'failed') {
    summary.lastErrors.push({
      ts: e.ts,
      message: e.message,
      task: e.task || null
    });
    if (summary.lastErrors.length > 20) summary.lastErrors.shift();
  }

  if (e.meta && e.meta.plan && e.meta.slug) {
    summary.componentPlans[e.meta.slug] = e.meta.plan;
  }
  if (e.meta) {
    const metaWithType = { ...e.meta };
    if (!metaWithType.type) {
      metaWithType.type = e.type;
    }
    if (!metaWithType.slug) {
      metaWithType.slug = e.slug || (metaWithType.meta_slug ?? undefined);
    }
    if (!metaWithType.phase) {
      metaWithType.phase = e.phase;
    }
    ingestCodingMeta(metaWithType, e.ts);
  }
}

function addEvent(e, broadcast = true) {
  eventsBuffer.push(e);
  if (eventsBuffer.length > MAX_BUFFER) eventsBuffer.shift();
  updateSummary(e);
  ingestComponentPlan(e);
  if (broadcast) broadcastSSE('log', e);
}

function eventsPerMinute() {
  const now = Date.now();
  const oneMinAgo = now - 60 * 1000;
  let count = 0;
  for (let i = summary.recentEventsTimestamps.length - 1; i >= 0; i--) {
    if (summary.recentEventsTimestamps[i] >= oneMinAgo) count++;
    else break;
  }
  return count;
}

function broadcastSSE(eventName, data) {
  const payload = `event: ${eventName}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of clients) {
    res.write(payload);
  }
}

function writePortFile(port) {
  const obj = {
    port,
    pid: process.pid,
    startedAt: new Date().toISOString(),
    url: `http://localhost:${port}`
  };
  const portFile = path.join(LOG_DIR, 'monitor.port');
  fs.writeFile(portFile, JSON.stringify(obj, null, 2), () => {});
}

function ingestComponentPlan(e) {
  if (!e.meta) return;
  const { plan, slug, plan_path: planPath } = e.meta;
  if (plan && slug) {
    summary.componentPlans[slug] = plan;
    ensureCodingBucket(slug);
    bootstrapPlanStrategies(slug, plan);
  }
  if (planPath && slug) {
    try {
      const diskPlan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
      summary.componentPlans[slug] = diskPlan;
      ensureCodingBucket(slug);
      bootstrapPlanStrategies(slug, diskPlan);
    } catch {
      // ignore read/parse failure
    }
  }
}

function ensureCodingBucket(slug) {
  if (!slug) return null;
  const existing = summary.codingStrategies[slug];
  if (existing && existing.tests) {
    return existing;
  }
  const bucket = existing || {};
  if (!bucket.tests) bucket.tests = {};
  summary.codingStrategies[slug] = bucket;
  return bucket;
}

function bootstrapPlanStrategies(slug, plan) {
  if (!slug || !plan) return;
  const bucket = ensureCodingBucket(slug);
  if (!bucket) return;
  const tests = bucket.tests || (bucket.tests = {});
  const entries = extractStrategiesFromPlan(plan) || [];
  entries.forEach((entry) => {
    if (!entry || !entry.id) return;
    const existing = tests[entry.id] || { strategy: [], files: [] };
    if (!existing.strategy?.length && entry.strategy?.length) {
      existing.strategy = entry.strategy;
    }
    if (!existing.files?.length && entry.files?.length) {
      existing.files = entry.files;
    }
    if (!existing.files?.length) {
      existing.files = guessDefaultTargets(slug);
    }
    existing.status = existing.status || entry.status || 'proposed';
    existing.normalized = normalizeKey(entry.id);
    tests[entry.id] = existing;
  });
}

function findTestId(record) {
  if (!record || typeof record !== 'object') return null;
  const keys = ['test_id', 'id', 'test', 'test_case', 'name', 'slug'];
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
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
      const candidates = ['summary', 'plan', 'text', 'description', 'notes'];
      for (const key of candidates) {
        if (key in input) {
          walk(input[key]);
        }
      }
    }
  };
  walk(value);
  return result;
}

function normalizeKey(value) {
  if (!value) return '';
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '');
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
      const candidates = ['files', 'file_paths', 'paths', 'targets', 'touched_files'];
      for (const key of candidates) {
        if (key in input) {
          walk(input[key]);
        }
      }
    }
  };
  walk(value);
  return result;
}

function guessDefaultTargets(slug) {
  if (!slug) return [];
  const safe = String(slug).replace(/[^a-z0-9_]+/gi, '_');
  const candidates = [
    path.join('src', `${safe}.py`),
    path.join('src', safe, '__init__.py'),
    path.join('src', safe),
    path.join('project_runtime', safe),
    path.join('tests', 'feature_specs', slug),
  ];
  const results = [];
  candidates.forEach((rel) => {
    const abs = path.join(REPO_ROOT, rel);
    if (fs.existsSync(abs)) {
      results.push(rel);
    }
  });
  if (!results.length) {
    results.push(path.join('src', `${safe}.py`));
  }
  return results;
}

function extractStrategyEntry(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = findTestId(raw);
  if (!id) return null;
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
  return { id, strategy, files, status, notes, source };
}

function summarizeStageHints(meta) {
  const hints = [];
  const identifier = (meta.identifier || '').toString();
  const description = (meta.description || '').toString().toLowerCase();
  const reason = (meta.failure_reason || '').toString();
  const tail = (meta.tail || '').toString();

  if (meta.ok === false) {
    if (tail.includes('SameFileError')) {
      hints.push('Guard requirements copy when source and destination are identical (skip duplicate shutil.copyfile).');
      hints.push('Re-run the failing unit grid after adding the guard.');
    } else if (identifier.startsWith('03.') || description.includes('unit')) {
      hints.push(reason || 'Investigate unit grid failures and fix failing tests.');
    } else if (identifier.startsWith('04.') || description.includes('coverage')) {
      hints.push('Add or broaden tests to raise src coverage to at least the 80% threshold.');
    } else if (identifier.startsWith('06.1') || description.includes('black')) {
      hints.push('Run black on the repo (or targeted modules) and commit formatting fixes.');
    } else if (identifier.startsWith('06.2') || description.includes('isort')) {
      hints.push('Apply isort to reorder imports (use --profile black for consistency).');
    } else if (identifier.startsWith('06.3') || description.includes('ruff')) {
      hints.push('Resolve ruff lint findings and re-run the style gate.');
    } else if (identifier.startsWith('06.4') || description.includes('flake8')) {
      hints.push(reason || 'Address flake8 style violations (e.g., line length, unused imports).');
    } else if (identifier.startsWith('06.5') || description.includes('mypy')) {
      hints.push('Fix typing issues reported by mypy and re-run the discriminator.');
    }
    if (!hints.length && reason) {
      hints.push(reason);
    }
  }

  return hints;
}

function extractStrategiesFromPlan(plan) {
  const entries = [];
  if (!plan || typeof plan !== 'object') return entries;
  const components = Array.isArray(plan.components) ? plan.components : [];
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    const tests = Array.isArray(node.tests) ? node.tests : [];
    tests.forEach((test) => {
      const entry = extractStrategyEntry(test);
      if (entry) entries.push(entry);
    });
    const subs = Array.isArray(node.subcomponents) ? node.subcomponents : [];
    subs.forEach(visit);
  };
  components.forEach(visit);
  return entries;
}

function extractStrategiesFromMeta(meta) {
  const entries = [];
  if (!meta || typeof meta !== 'object') return entries;
  const direct = extractStrategyEntry(meta);
  if (direct) entries.push(direct);
  const candidateKeys = ['strategy', 'strategies', 'entries', 'updates', 'tests'];
  candidateKeys.forEach((key) => {
    const value = meta[key];
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach((item) => {
        const entry = extractStrategyEntry(item);
        if (entry) entries.push(entry);
      });
    } else if (typeof value === 'object') {
      const entry = extractStrategyEntry(value);
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
      entries.push(...extractStrategiesFromPlan(plan));
    }
  }
  if (meta.plan_path) {
    try {
      const planPath = path.isAbsolute(meta.plan_path)
        ? meta.plan_path
        : path.join(REPO_ROOT, meta.plan_path);
      if (fs.existsSync(planPath)) {
        const diskPlan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
        entries.push(...extractStrategiesFromPlan(diskPlan));
      }
    } catch {
      // ignore
    }
  }
  return entries;
}

function storeStrategyEntries(slug, entries, ts) {
  const bucket = ensureCodingBucket(slug);
  if (!bucket) return;
  const tests = bucket.tests || (bucket.tests = {});
  entries.forEach((entry) => {
    if (!entry || !entry.id) return;
    const existing = tests[entry.id] || { strategy: [], files: [] };
    if (entry.strategy && entry.strategy.length) {
      existing.strategy = entry.strategy;
    }
    if (entry.files && entry.files.length) {
      const merged = new Set(existing.files || []);
      entry.files.forEach((file) => merged.add(file));
      existing.files = Array.from(merged);
    }
    if (entry.status) existing.status = entry.status;
    if (entry.notes) existing.notes = entry.notes;
    if (entry.source) existing.source = entry.source;
    existing.lastUpdated = ts;
    existing.normalized = normalizeKey(entry.id);
    tests[entry.id] = existing;
  });
}

function appendStep(entry, text) {
  if (!text) return;
  const trimmed = String(text).trim();
  if (!trimmed) return;
  entry.strategy = entry.strategy || [];
  const key = normalizeKey(trimmed);
  const existingKeys = new Set(entry.strategy.map((step) => normalizeKey(step)));
  if (!existingKeys.has(key)) {
    entry.strategy.push(trimmed);
  }
}

function mergeFiles(entry, files) {
  if (!files || !files.length) return;
  entry.files = entry.files || [];
  const merged = new Set(entry.files);
  files.forEach((file) => {
    if (file) merged.add(String(file));
  });
  entry.files = Array.from(merged);
}

function findMatchingTestKey(tests, candidate) {
  if (!candidate) return null;
  if (candidate in tests) return candidate;
  const normalizedCandidate = normalizeKey(candidate);
  for (const key of Object.keys(tests)) {
    const entry = tests[key] || {};
    const normalized = entry.normalized || normalizeKey(key);
    if (normalized && normalized === normalizedCandidate) {
      return key;
    }
  }
  return null;
}

function applyStrategyUpdate(slug, testIds, ts, updater) {
  const bucket = ensureCodingBucket(slug);
  if (!bucket) return;
  const tests = bucket.tests || (bucket.tests = {});
  const targetIds = testIds && testIds.length ? testIds : Object.keys(tests);
  if (!targetIds.length) {
    const key = "__global__";
    const entry = tests[key] || { strategy: [], files: [] };
    entry.normalized = entry.normalized || normalizeKey(key);
    updater(entry, key);
    entry.lastUpdated = ts;
    tests[key] = entry;
    return;
  }
  targetIds.forEach((candidate) => {
    const matchKey = findMatchingTestKey(tests, candidate);
    if (!matchKey && !(candidate in tests) && Object.keys(tests).length) {
      Object.keys(tests).forEach((key) => {
        const entry = tests[key] || { strategy: [], files: [] };
        entry.normalized = entry.normalized || normalizeKey(key);
        updater(entry, key);
        entry.lastUpdated = ts;
        tests[key] = entry;
      });
      return;
    }
    const key = matchKey || candidate;
    const entry = tests[key] || { strategy: [], files: [] };
    entry.normalized = entry.normalized || normalizeKey(key);
    updater(entry, key);
    entry.lastUpdated = ts;
    tests[key] = entry;
  });
}

function ingestCodingMeta(meta, ts) {
  if (!meta || typeof meta !== 'object') return;
  if (meta.phase !== 'discriminator') return;
  const slug = meta.slug;
  if (!slug) return;

  const genericEntries = extractStrategiesFromMeta(meta);
  if (genericEntries.length > 0) {
    storeStrategyEntries(slug, genericEntries, ts);
  }

  const type = meta.type;
  if (!type) return;

  if (type === 'mechanical_fixes' && meta.changed) {
    const tools = Array.isArray(meta.tools) ? meta.tools.join(', ') : 'style tools';
    const targets = Array.isArray(meta.targets) ? meta.targets : [];
    applyStrategyUpdate(slug, null, ts, (entry) => {
      appendStep(entry, `Applied mechanical fixes (${tools})`);
      mergeFiles(entry, targets);
      entry.status = entry.status || 'in_progress';
    });
    return;
  }

  if (type === 'llm_patch_decision' && meta.accepted) {
    const files = Array.isArray(meta.files) ? meta.files : [];
    applyStrategyUpdate(slug, null, ts, (entry) => {
      appendStep(entry, `Committed discriminator patch (${meta.reason || 'update'})`);
      mergeFiles(entry, files);
      if (entry.status === 'failed') {
        entry.status = 'in_progress';
      }
    });
    return;
  }

  if (type === 'stage_end') {
    const hints = summarizeStageHints(meta) || [];
    if (meta.ok === false) {
      const files = Array.isArray(meta.failed_files) ? meta.failed_files : [];
      applyStrategyUpdate(slug, null, ts, (entry) => {
        hints.forEach((hint) => appendStep(entry, hint));
        mergeFiles(entry, files);
        entry.status = 'failed';
      });
    } else if (meta.ok) {
      const command = meta.command || '';
      const isPytestStage = typeof command === 'string' && command.includes('pytest');
      if (isPytestStage) {
        applyStrategyUpdate(slug, null, ts, (entry) => {
          entry.status = 'pass';
        });
      }
    }
    return;
  }

  if (type === 'run_completed') {
    if (meta.ok) {
      applyStrategyUpdate(slug, null, ts, (entry) => {
        entry.status = 'pass';
      });
    }
    return;
  }
}

function loadComponentPlansFromDisk() {
  let entries = [];
  try {
    entries = fs.readdirSync(COMPONENT_PLAN_DIR);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.startsWith('component_plan_') || !entry.endsWith('.json')) continue;
    const slug = entry.replace('component_plan_', '').replace(/\.json$/, '');
    const filePath = path.join(COMPONENT_PLAN_DIR, entry);
    try {
      const plan = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      summary.componentPlans[slug] = plan;
      ensureCodingBucket(slug);
    } catch {
      // ignore file errors
    }
  }
}

async function prefillBuffer() {
  // Read last ~128KB to warm the buffer
  const approxBytes = 128 * 1024;
  try {
    const stat = await fsp.stat(EVENTS_FILE);
    const start = Math.max(0, stat.size - approxBytes);
    const fh = await fsp.open(EVENTS_FILE, 'r');
    const buf = Buffer.alloc(stat.size - start);
    await fh.read(buf, 0, buf.length, start);
    await fh.close();
    const lines = buf.toString('utf8').split(/\r?\n/).filter(Boolean);
    lines.forEach(line => {
      const e = normalizeEvent(line);
      if (e) addEvent(e, false);
    });
    loadComponentPlansFromDisk();
  } catch {
    // ignore; file might be empty or not readable yet
  }
}

function startTailing(file) {
  let position = 0;
  let watching = false;
  let fd = null;
  const openAndSeek = async () => {
    try {
      const stat = await fsp.stat(file);
      position = stat.size; // start tailing new data only
      fd = await fsp.open(file, 'r');
    } catch {
      // Create file and try again later
      ensureFileSync(file);
      setTimeout(openAndSeek, 500);
      return;
    }
    if (!watching) {
      watching = true;
      fs.watch(file, { persistent: true }, async (evt) => {
        if (evt !== 'change') return;
        await readNewData();
      });
    }
  };

  const readNewData = async () => {
    try {
      const stat = await fsp.stat(file);
      if (stat.size < position) {
        // truncated/rotated
        position = 0;
      }
      const len = stat.size - position;
      if (len <= 0) return;
      const buf = Buffer.alloc(len);
      await fd.read(buf, 0, len, position);
      position = stat.size;
      const chunk = buf.toString('utf8');
      const lines = chunk.split(/\r?\n/);
      for (const line of lines) {
        if (!line.trim()) continue;
        const e = normalizeEvent(line);
        if (e) addEvent(e, true);
      }
      // also broadcast a summary tick occasionally
      broadcastSSE('summary', getSummaryDTO());
    } catch (err) {
      // fd may be invalid after rotations; reopen
      try {
        await fd?.close();
      } catch {}
      fd = await fsp.open(file, 'r');
      await readNewData();
    }
  };

  openAndSeek();
}

function getSummaryDTO() {
  try {
    if (summary.componentPlans) {
      for (const [slug, plan] of Object.entries(summary.componentPlans)) {
        bootstrapPlanStrategies(slug, plan);
      }
    }
  } catch {
    // ignore bootstrap errors while composing summary
  }
  return {
    startedAt: summary.startedAt,
    lastEventAt: summary.lastEventAt,
    totals: summary.totals,
    tasks: summary.tasks,
    lastErrors: summary.lastErrors,
    eventsPerMinute: eventsPerMinute(),
    componentPlans: JSON.parse(JSON.stringify(summary.componentPlans)),
    codingStrategies: JSON.parse(JSON.stringify(summary.codingStrategies))
  };
}

// ====== API ======
app.get('/api/health', (_req, res) => {
  res.json({ ok: true, startedAt: summary.startedAt, file: EVENTS_FILE });
});

app.get('/api/summary', (_req, res) => {
  res.json(getSummaryDTO());
});

app.get('/api/events', (req, res) => {
  const limit = Math.min(parseInt(req.query.limit || '200', 10), MAX_BUFFER);
  const items = eventsBuffer.slice(-limit);
  res.json({ count: items.length, items });
});

// SSE stream
app.get('/api/stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders?.();

  // initial push: summary
  res.write(`event: summary\ndata: ${JSON.stringify(getSummaryDTO())}\n\n`);

  clients.add(res);
  req.on('close', () => {
    clients.delete(res);
  });
});

// Fallback to index.html for any route
app.get('*', (_req, res) => {
  res.sendFile(path.join(STATIC_DIR, 'index.html'));
});

// ====== Boot ======
(async () => {
  await prefillBuffer();
  startTailing(EVENTS_FILE);

  const server = http.createServer(app);
  const basePort = Number.isNaN(Number(PORT_ENV)) ? 4321 : Number(PORT_ENV);
  const maxAttempts = Number.isNaN(PORT_RETRY_LIMIT) ? 15 : Math.max(1, PORT_RETRY_LIMIT);

  function attemptListen(attempt) {
    const desiredPort = basePort + attempt;
    const handleError = (err) => {
      server.removeListener('error', handleError);
      if (err && err.code === 'EADDRINUSE' && attempt + 1 < maxAttempts) {
        console.warn(`[monitor] Port ${desiredPort} in use; trying ${desiredPort + 1}`);
        setImmediate(() => attemptListen(attempt + 1));
        return;
      }
      console.error('[monitor] Failed to start UI server:', err?.message || err);
      process.exit(1);
    };

    server.once('error', handleError);
    server.listen(desiredPort, () => {
      server.removeListener('error', handleError);
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : desiredPort;
      writePortFile(port);
      const url = `http://localhost:${port}`;
      console.log(`[monitor] UI listening at ${url}`);
      console.log(`[monitor] Tailing: ${EVENTS_FILE}`);
      if (OPEN_BROWSER) openInBrowser(url);
    });
  }

  attemptListen(0);
})();

function openInBrowser(url) {
  const { spawn } = require('node:child_process');
  const cmd = process.platform === 'darwin' ? 'open'
    : process.platform === 'win32' ? 'start'
      : 'xdg-open';
  spawn(cmd, [url], { detached: true, stdio: 'ignore', shell: true }).unref();
}
