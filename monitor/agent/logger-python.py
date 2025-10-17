# Minimal JSONL logger for your agent (Python)
import datetime
import json
import os


class AgentLogger:
    def __init__(self, log_dir=None):
        if log_dir is None:
            log_dir = os.path.join(os.getcwd(), ".agent", "logs")
        os.makedirs(log_dir, exist_ok=True)
        self.file = os.path.join(log_dir, "events.jsonl")

    def _write(self, obj):
        event = {"ts": datetime.datetime.utcnow().isoformat() + "Z"}
        event.update(obj)
        with open(self.file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def info(self, message, meta=None):
        self._write({"level": "info", "message": message, "meta": meta})

    def warn(self, message, meta=None):
        self._write({"level": "warn", "message": message, "meta": meta})

    def error(self, message, meta=None):
        self._write({"level": "error", "message": message, "meta": meta})

    def debug(self, message, meta=None):
        self._write({"level": "debug", "message": message, "meta": meta})

    def task_start(self, task, meta=None):
        self._write(
            {
                "level": "task",
                "task": task,
                "status": "started",
                "message": f"Start {task}",
                "meta": meta,
            }
        )

    def task_progress(self, task, progress, meta=None):
        bounded = float(max(0, min(1, progress)))
        self._write(
            {
                "level": "progress",
                "task": task,
                "status": "progress",
                "progress": bounded,
                "message": f"Progress {task}: {round(bounded * 100)}%",
                "meta": meta,
            }
        )

    def task_done(self, task, meta=None):
        self._write(
            {
                "level": "task",
                "task": task,
                "status": "completed",
                "message": f"Done {task}",
                "meta": meta,
            }
        )

    def task_fail(self, task, message, meta=None):
        self._write(
            {
                "level": "error",
                "task": task,
                "status": "failed",
                "message": message,
                "meta": meta,
            }
        )
