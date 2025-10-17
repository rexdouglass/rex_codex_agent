// Minimal JSONL logger for your agent
const fs = require('node:fs');
const path = require('node:path');

function createLogger({ logDir = path.join(process.cwd(), '.agent', 'logs') } = {}) {
  const file = path.join(logDir, 'events.jsonl');
  fs.mkdirSync(logDir, { recursive: true });
  const stream = fs.createWriteStream(file, { flags: 'a' });

  function write(obj) {
    const e = {
      ts: new Date().toISOString(),
      ...obj
    };
    stream.write(JSON.stringify(e) + '\n');
  }

  return {
    info: (message, meta) => write({ level: 'info', message, meta }),
    warn: (message, meta) => write({ level: 'warn', message, meta }),
    error: (message, meta) => write({ level: 'error', message, meta }),
    debug: (message, meta) => write({ level: 'debug', message, meta }),
    taskStart: (task, meta) => write({ level: 'task', task, status: 'started', message: `Start ${task}`, meta }),
    taskProgress: (task, progress, meta) => write({
      level: 'progress',
      task,
      status: 'progress',
      progress,
      message: `Progress ${task}: ${Math.round(progress * 100)}%`,
      meta
    }),
    taskDone: (task, meta) => write({ level: 'task', task, status: 'completed', message: `Done ${task}`, meta }),
    taskFail: (task, message, meta) => write({ level: 'error', task, status: 'failed', message, meta })
  };
}

module.exports = { createLogger };
