#!/usr/bin/env node
/**
 * Launches the monitor server if not already running.
 * - Reuses existing server if monitor.port responds to /api/health
 * - Otherwise spawns `node server.js` detached
 * - Prints the URL to stdout
 */
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');

const SOURCE_ROOT = process.cwd();
const TARGET_ROOT = process.env.REPO_ROOT
  ? path.resolve(process.env.REPO_ROOT)
  : SOURCE_ROOT;
const MONITOR_ROOT = path.join(SOURCE_ROOT, 'monitor');
const LOG_DIR = process.env.LOG_DIR || path.join(TARGET_ROOT, '.agent', 'logs');
const PORT_FILE = path.join(LOG_DIR, 'monitor.port');

(async function main() {
  await fsp.mkdir(LOG_DIR, { recursive: true });

  const reuse = await tryReuseExisting();
  if (reuse) {
    console.log(reuse.url);
    process.exit(0);
  }

  const env = {
    ...process.env,
    LOG_DIR,
    MONITOR_PORT: process.env.MONITOR_PORT || '4321',
    OPEN_BROWSER: process.env.OPEN_BROWSER || 'false'
  };

  const serverPath = path.join(MONITOR_ROOT, 'server.js');
  const args = [serverPath];
  const detached = process.argv.includes('--background');

  const child = spawn(process.execPath, args, {
    env,
    cwd: MONITOR_ROOT,
    detached,
    stdio: detached ? 'ignore' : 'inherit',
    windowsHide: true
  });

  if (detached) child.unref();

  // Wait a bit, then read port file
  setTimeout(async () => {
    const info = await readPortFile();
    if (info) console.log(info.url);
    else console.log('http://localhost:4321');
    if (!detached) process.exit(0);
  }, 800);
})();

async function tryReuseExisting() {
  try {
    const txt = await fsp.readFile(PORT_FILE, 'utf8');
    const info = JSON.parse(txt);
    const res = await fetch(`http://localhost:${info.port}/api/health`).then(r => r.json());
    if (res && res.ok) return info;
  } catch {
    // ignore; file may not exist or server is down
  }
  return null;
}

async function readPortFile() {
  try {
    const txt = await fsp.readFile(PORT_FILE, 'utf8');
    return JSON.parse(txt);
  } catch {
    return null;
  }
}
