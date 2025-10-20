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
const { spawn } = require('node:child_process');
const express = require('express');
const compression = require('compression');
const helmet = require('helmet');

const LOG_DIR = process.env.LOG_DIR || path.join(process.cwd(), '.agent', 'logs');
const EVENTS_FILE = process.env.EVENTS_FILE || path.join(LOG_DIR, 'events.jsonl');
const REPO_ROOT =
  process.env.REPO_ROOT
    ? path.resolve(process.env.REPO_ROOT)
    : path.resolve(LOG_DIR, '..', '..');
const STATIC_DIR = path.join(__dirname, 'public');
const COMPONENT_PLAN_DIR = path.join(REPO_ROOT, '.codex_ci');
const FEATURE_CARD_DIR = path.join(REPO_ROOT, 'documents', 'feature_cards');

const PORT_ENV = process.env.MONITOR_PORT || process.env.PORT || 4321;
const OPEN_BROWSER = (process.env.OPEN_BROWSER || 'false').toLowerCase() === 'true';
const PORT_RETRY_LIMIT = Number(process.env.MONITOR_PORT_RETRIES || '15');

ensureDirSync(LOG_DIR);
ensureDirSync(COMPONENT_PLAN_DIR);
ensureFileSync(EVENTS_FILE);

const app = express();
app.disable('x-powered-by');
app.use(
  helmet({
    contentSecurityPolicy: {
      useDefaults: true,
      directives: {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "connect-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
      }
    },
    referrerPolicy: { policy: 'no-referrer' },
    crossOriginEmbedderPolicy: false,
    crossOriginOpenerPolicy: { policy: 'same-origin' },
    crossOriginResourcePolicy: { policy: 'same-origin' }
  })
);
app.use(compression());
app.use(express.json({ limit: '64kb' }));
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
  codingStrategies: {}, // { [slug]: { tests: { [testId]: entry } } }
  featureCards: [] // [{ slug, title, status, path }]
};

// ====== Utilities ======
function ensureDirSync(p) {
  fs.mkdirSync(p, { recursive: true });
}
function ensureFileSync(p) {
  if (!fs.existsSync(p)) fs.writeFileSync(p, '');
}

function parseFeatureCard(pathname) {
  const slug = path.basename(pathname, path.extname(pathname));
  if (!slug || slug.toLowerCase() === 'readme') return null;
  const absolute = path.join(FEATURE_CARD_DIR, pathname);
  let title = slug.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  let status = 'unknown';
  try {
    const raw = fs.readFileSync(absolute, 'utf8');
    const lines = raw.split(/\r?\n/);
    for (const line of lines) {
      if (line.startsWith('# ')) {
        const candidate = line.slice(2).trim();
        if (candidate) title = candidate;
        break;
      }
    }
    for (const line of lines) {
      const match = line.match(/^status\s*:\s*(.+)$/i);
      if (match && match[1]) {
        status = match[1].trim().toLowerCase() || status;
        break;
      }
    }
  } catch {
    // ignore unreadable cards
  }
  return {
    slug,
    title,
    status,
    path: path.relative(REPO_ROOT, absolute)
  };
}

function listFeatureCards() {
  let entries = [];
  try {
    entries = fs.readdirSync(FEATURE_CARD_DIR);
  } catch {
    return [];
  }
  const cards = [];
  for (const entry of entries) {
    if (!entry.toLowerCase().endsWith('.md')) continue;
    const meta = parseFeatureCard(entry);
    if (!meta) continue;
    cards.push(meta);
  }
  cards.sort((a, b) => a.slug.localeCompare(b.slug));
  summary.featureCards = cards;
  return cards;
}

const AGENT_STATE_PATH = path.join(REPO_ROOT, 'rex-agent.json');
const COMMAND_TIMEOUT_MS = Number(process.env.REX_MONITOR_ACTION_TIMEOUT || '300000');

function readAgentState() {
  try {
    const raw = fs.readFileSync(AGENT_STATE_PATH, 'utf8');
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function summariseAgentState() {
  const snapshot = readAgentState();
  const llm = snapshot && typeof snapshot.llm === 'object' ? snapshot.llm : null;
  const doctor = snapshot && typeof snapshot.doctor === 'object' ? snapshot.doctor : null;
  const scaffolding = snapshot && typeof snapshot.scaffolding === 'object' ? snapshot.scaffolding : null;
  const preflight = snapshot && typeof snapshot.preflight === 'object' ? snapshot.preflight : null;
  const hello = preflight && typeof preflight.codex_hello === 'object' ? preflight.codex_hello : null;
  const scaffoldCount =
    scaffolding && Array.isArray(scaffolding.records) ? scaffolding.records.length : 0;
  const ledgerDir = path.join(REPO_ROOT, 'documents', 'assumption_ledgers');
  let assumptionFiles = [];
  try {
    assumptionFiles = fs
      .readdirSync(ledgerDir)
      .filter((name) =>
        /\.(md|markdown|txt)$/i.test(name) && name.toLowerCase() !== 'readme.md'
      );
  } catch {
    assumptionFiles = [];
  }
  return {
    llm,
    doctor,
    scaffolding: {
      records: scaffoldCount,
    },
    assumptions: {
      count: assumptionFiles.length,
      files: assumptionFiles,
    },
    preflight: {
      codex_hello: hello || null,
    },
  };
}

function sanitizeSlug(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const normalized = trimmed.replace(/\.md$/i, '');
  if (!/^[A-Za-z0-9_-]+$/.test(normalized)) return null;
  return normalized;
}

function featureCardPath(slug) {
  const safe = sanitizeSlug(slug);
  if (!safe) return null;
  const candidate = path.join(REPO_ROOT, 'documents', 'feature_cards', `${safe}.md`);
  return fs.existsSync(candidate) ? candidate : null;
}

function runRexCommand(args, { timeoutMs = COMMAND_TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn('./rex-codex', args, {
      cwd: REPO_ROOT,
      env: process.env,
    });

    let stdout = '';
    let stderr = '';
    let killed = false;
    const limit = 20000;
    const append = (buffer, chunk) => {
      const next = buffer + chunk;
      return next.length > limit ? next.slice(next.length - limit) : next;
    };

    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            killed = true;
            proc.kill('SIGTERM');
          }, timeoutMs)
        : null;

    proc.stdout?.on('data', (chunk) => {
      stdout = append(stdout, chunk.toString());
    });
    proc.stderr?.on('data', (chunk) => {
      stderr = append(stderr, chunk.toString());
    });
    proc.on('error', (err) => {
      if (timer) clearTimeout(timer);
      reject(err);
    });
    proc.on('close', (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, stdout, stderr, killed });
    });
  });
}

const ACTION_HANDLERS = {
  'run-doctor': async () => runRexCommand(['doctor']),
  'rerun-generator': async ({ slug }) => {
    const safe = sanitizeSlug(slug);
    if (!safe) {
      throw Object.assign(new Error('Invalid slug for generator run'), { statusCode: 400 });
    }
    const card = featureCardPath(safe);
    if (!card) {
      throw Object.assign(new Error(`Feature Card not found for slug ${safe}`), { statusCode: 404 });
    }
    return runRexCommand(['generator', card, '--single-pass']);
  },
  scaffold: async ({ slug, force }) => {
    const safe = sanitizeSlug(slug);
    if (!safe) {
      throw Object.assign(new Error('Invalid slug for scaffolding'), { statusCode: 400 });
    }
    const args = ['scaffold', safe];
    if (force) args.push('--force');
    return runRexCommand(args);
  },
};

async function executeAction(payload) {
  const { action, ...rest } = payload || {};
  if (typeof action !== 'string') {
    throw Object.assign(new Error('Missing action type'), { statusCode: 400 });
  }
  const handler = ACTION_HANDLERS[action];
  if (!handler) {
    throw Object.assign(new Error(`Unknown action: ${action}`), { statusCode: 400 });
  }
  const result = await handler(rest);
  broadcastSSE('summary', getSummaryDTO());
  console.log(
    `[monitor] action ${action} → exit ${result.code}`
  );
  return {
    ok: result.code === 0,
    code: result.code,
    stdout: result.stdout,
    stderr: result.stderr,
    killed: result.killed,
  };
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
    const meta = e.meta && typeof e.meta === 'object' ? e.meta : {};
    const rawExit =
      meta.exit_code ??
      meta.exitCode ??
      meta.returncode ??
      meta.returnCode ??
      null;
    const exitCode = rawExit !== null && rawExit !== undefined && rawExit !== ''
      ? Number(rawExit)
      : null;
    const rawIter = meta.iteration ?? meta.pass ?? meta.generation_pass ?? null;
    const iteration = rawIter !== null && rawIter !== undefined && rawIter !== ''
      ? Number(rawIter)
      : null;
    const missing = Array.isArray(meta.missing)
      ? meta.missing.slice(0, 6)
      : undefined;
    const violations = Array.isArray(meta.violations)
      ? meta.violations.slice(0, 6)
      : undefined;
    const reason = typeof meta.reason === 'string'
      ? meta.reason
      : typeof meta.failure_reason === 'string'
        ? meta.failure_reason
        : undefined;
    const hint = typeof meta.hint === 'string'
      ? meta.hint
      : typeof meta.guidance === 'string'
        ? meta.guidance
        : undefined;
    const statusLabel = e.status || meta.status || null;
    const slug = typeof meta.slug === 'string' && meta.slug
      ? meta.slug
      : typeof e.slug === 'string' && e.slug
        ? e.slug
        : null;
    const actions = computeErrorActions({
      exitCode: Number.isFinite(exitCode) ? exitCode : null,
      slug,
      missing,
      violations,
      reason,
    });
    summary.lastErrors.push({
      ts: e.ts,
      message: e.message,
      task: e.task || slug,
      status: typeof statusLabel === 'string' ? statusLabel : null,
      reason: reason || null,
      hint: hint || null,
      exitCode: Number.isFinite(exitCode) ? exitCode : null,
      iteration: Number.isFinite(iteration) ? iteration : null,
      slug,
      missing,
      violations,
      actions,
      loginAttempted: Boolean(meta.login_attempted)
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
  const logEvent = sanitizeEventForLog(e);
  eventsBuffer.push(logEvent);
  if (eventsBuffer.length > MAX_BUFFER) eventsBuffer.shift();
  updateSummary(e);
  ingestComponentPlan(e);
  if (broadcast) broadcastSSE('log', logEvent);
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

function computeErrorActions(meta) {
  const actions = [];
  const dedupe = new Set();
  const add = (action) => {
    const key = JSON.stringify(action);
    if (!dedupe.has(key)) {
      dedupe.add(key);
      actions.push(action);
    }
  };
  if (meta && meta.slug) {
    add({
      action: 'scaffold',
      slug: meta.slug,
      label: `Scaffold ${meta.slug}`,
    });
    add({
      action: 'rerun-generator',
      slug: meta.slug,
      label: 'Re-run generator',
    });
  }
  const doctorHint =
    (meta && meta.exitCode === 8) ||
    (Array.isArray(meta?.missing) &&
      meta.missing.some((item) => typeof item === 'string' && item.includes('rex-codex doctor'))) ||
    (typeof meta?.reason === 'string' &&
      meta.reason.toLowerCase().includes('doctor'));
  if (doctorHint) {
    add({ action: 'run-doctor', label: 'Run ./rex-codex doctor' });
  }
  return actions;
}

function sanitizeEventForLog(e) {
  if (!e || typeof e !== 'object') return e;
  const copy = {
    ts: e.ts,
    level: e.level,
    message: e.message,
    task: e.task,
    status: e.status,
    progress: e.progress,
    slug: e.slug,
    phase: e.phase,
    type: e.type,
  };
  if (e.meta && typeof e.meta === 'object') {
    const meta = { ...e.meta };
    delete meta.plan;
    delete meta.playbook_snapshot;
    delete meta.repository_inventory;
    delete meta.components;
    delete meta.tests;
    delete meta.paths;
    if (Object.keys(meta).length > 0) {
      copy.meta = meta;
    }
  }
  return copy;
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
  const featureCards = listFeatureCards();
  const componentPlans = JSON.parse(JSON.stringify(summary.componentPlans));
  featureCards.forEach((card) => {
    const existing = componentPlans[card.slug];
    if (existing) {
      if (typeof existing.title !== 'string' || !existing.title) {
        existing.title = card.title;
      }
      if (typeof existing.status !== 'string' || !existing.status) {
        existing.status = card.status;
      }
      if (!existing.card_path) {
        existing.card_path = card.path;
      }
      if (!Array.isArray(existing.components)) {
        existing.components = [];
      }
    } else {
      componentPlans[card.slug] = {
        title: card.title,
        status: card.status,
        card_path: card.path,
        generated_at: null,
        components: []
      };
    }
  });

  return {
    startedAt: summary.startedAt,
    lastEventAt: summary.lastEventAt,
    totals: summary.totals,
    tasks: summary.tasks,
    lastErrors: summary.lastErrors,
    eventsPerMinute: eventsPerMinute(),
    componentPlans,
    codingStrategies: JSON.parse(JSON.stringify(summary.codingStrategies)),
    featureCards,
    agent: summariseAgentState(),
  };
}

// ====== API ======
app.post('/api/actions', async (req, res) => {
  try {
    const result = await executeAction(req.body || {});
    res.json(result);
  } catch (err) {
    const status = err && typeof err.statusCode === 'number' ? err.statusCode : 500;
    const message =
      err && typeof err.message === 'string' && err.message
        ? err.message
        : 'Action failed';
    res.status(status).json({ error: message });
  }
});

app.get('/api/health', (_req, res) => {
  const agent = summariseAgentState();
  const doctorStatus = agent && agent.doctor && agent.doctor.status;
  const ok = typeof doctorStatus === 'string' ? doctorStatus.toLowerCase() !== 'error' : true;
  res.json({
    ok,
    startedAt: summary.startedAt,
    lastEventAt: summary.lastEventAt,
    eventsPerMinute: eventsPerMinute(),
    totals: summary.totals,
    file: EVENTS_FILE,
    agent,
  });
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
