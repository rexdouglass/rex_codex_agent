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
  componentPlans: {} // { [slug]: plan }
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
      meta: json.meta || json.data || undefined
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
  }
  if (planPath && slug) {
    try {
      const diskPlan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
      summary.componentPlans[slug] = diskPlan;
    } catch {
      // ignore read/parse failure
    }
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
  return {
    startedAt: summary.startedAt,
    lastEventAt: summary.lastEventAt,
    totals: summary.totals,
    tasks: summary.tasks,
    lastErrors: summary.lastErrors,
    eventsPerMinute: eventsPerMinute(),
    componentPlans: JSON.parse(JSON.stringify(summary.componentPlans))
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
  const chosenPort = isNaN(Number(PORT_ENV)) ? 4321 : Number(PORT_ENV);
  server.listen(chosenPort, () => {
    const address = server.address();
    const port = typeof address === 'object' && address ? address.port : chosenPort;
    writePortFile(port);
    const url = `http://localhost:${port}`;
    console.log(`[monitor] UI listening at ${url}`);
    console.log(`[monitor] Tailing: ${EVENTS_FILE}`);
    if (OPEN_BROWSER) openInBrowser(url);
  });
})();

function openInBrowser(url) {
  const { spawn } = require('node:child_process');
  const cmd = process.platform === 'darwin' ? 'open'
    : process.platform === 'win32' ? 'start'
      : 'xdg-open';
  spawn(cmd, [url], { detached: true, stdio: 'ignore', shell: true }).unref();
}
