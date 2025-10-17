import fs from "fs";
import path from "path";

import type { EventLogEntry, RawEvent, SummaryEntry } from "./types.js";

export type OutlineNode = {
  id: string;
  title: string;
  tests: string[];
  pending: string[];
};

export type CoverageMeter = {
  linked: number;
  total: number;
};

export type CoverageState = {
  specToTest: CoverageMeter;
  unit: CoverageMeter;
  integration: CoverageMeter;
};

export type ChecksState = {
  lint?: "pass" | "fail";
  typecheck?: "pass" | "fail";
  build?: "pass" | "fail" | "skip";
};

export type DiffState = {
  path: string;
  lines: string[];
  explain: string[];
};

export type ReducerOptions = {
  targetSlug?: string;
  diffFile?: string;
  projectTitle?: string;
};

export type State = {
  slug?: string;
  projectTitle: string;
  featureTitle: string;
  outlineOrder: string[];
  outline: Record<string, OutlineNode>;
  pendingTests: Set<string>;
  testFrozen: Set<string>;
  orphans: string[];
  coverage: CoverageState;
  checks: ChecksState;
  novelty: number;
  loop: "green" | "yellow" | "red";
  events: EventLogEntry[];
  summaries: SummaryEntry[];
  lastDiff?: DiffState;
};

export const initialState: State = {
  projectTitle: path.basename(process.cwd()),
  featureTitle: "Feature Card",
  outlineOrder: [],
  outline: {},
  pendingTests: new Set<string>(),
  testFrozen: new Set<string>(),
  orphans: [],
  coverage: {
    specToTest: { linked: 0, total: 0 },
    unit: { linked: 0, total: 0 },
    integration: { linked: 0, total: 0 },
  },
  checks: { build: "skip" },
  novelty: 0,
  loop: "green",
  events: [],
  summaries: [],
};

const MAX_LOG_EVENTS = 60;

function summarise(raw: RawEvent): string {
  const data = raw.data ?? {};
  switch (raw.type) {
    case "feature_started":
      return `feature_started ${data?.["title"] ?? ""}`.trim();
    case "iteration_started":
      return `iteration_started ${data?.["iteration"] ?? ""}`;
    case "iteration_completed":
      return `iteration_completed exit=${data?.["exit_code"] ?? ""}`;
    case "diff_summary": {
      const files = Array.isArray(data?.["files"])
        ? (data["files"] as unknown[])
        : [];
      return `diff_summary ${files.length} file(s)`;
    }
    case "spec_trace_update":
      return "spec_trace_update";
    case "pytest_snapshot":
      return `pytest_snapshot ${data?.["status"] ?? ""}`;
    case "feature_completed":
      return "feature_completed";
    case "feature_failed":
      return `feature_failed ${data?.["reason"] ?? ""}`;
    case "critic_guidance":
      return `critic_guidance ${data?.["done"] ? "done" : "todo"}`;
    default:
      return raw.type;
  }
}

function ensureOutlineNode(state: State, id: string, title: string): OutlineNode {
  const existing = state.outline[id];
  if (existing) {
    if (title && existing.title === existing.id) {
      existing.title = title;
    }
    return existing;
  }
  const node: OutlineNode = {
    id,
    title: title || id,
    tests: [],
    pending: [],
  };
  state.outline[id] = node;
  state.outlineOrder.push(id);
  return node;
}

function readDiffLines(diffPath: string | undefined): string[] {
  if (!diffPath) {
    return [];
  }
  try {
    const text = fs.readFileSync(diffPath, "utf-8");
    return text.split(/\r?\n/).slice(0, 12);
  } catch {
    return [];
  }
}

function unique(array: string[]): string[] {
  return Array.from(new Set(array));
}

function normalizeTestName(name: unknown): string {
  if (typeof name !== "string") {
    return "";
  }
  return name.trim();
}

export function reduce(
  state: State,
  raw: RawEvent,
  options: ReducerOptions,
): State {
  const phase = raw.phase ?? "generator";
  if (phase !== "generator") {
    return state;
  }
  const eventSlug = typeof raw.slug === "string" ? raw.slug : undefined;
  const filterSlug = options.targetSlug;
  if (filterSlug && eventSlug && eventSlug !== filterSlug) {
    return state;
  }
  const slug = eventSlug ?? state.slug ?? filterSlug ?? undefined;
  if (!slug) {
    return state;
  }
  const next: State = {
    ...state,
    slug,
    outline: { ...state.outline },
    outlineOrder: [...state.outlineOrder],
    pendingTests: new Set(state.pendingTests),
    testFrozen: new Set(state.testFrozen),
    coverage: {
      specToTest: { ...state.coverage.specToTest },
      unit: { ...state.coverage.unit },
      integration: { ...state.coverage.integration },
    },
    checks: { ...state.checks },
    events: [...state.events],
    summaries: [...state.summaries],
    orphans: [...state.orphans],
  };
  if (options.projectTitle) {
    next.projectTitle = options.projectTitle;
  }

  const summary = summarise(raw);
  const logEntry: EventLogEntry = { ts: raw.ts, type: raw.type, summary };
  next.events.push(logEntry);
  if (next.events.length > MAX_LOG_EVENTS) {
    next.events = next.events.slice(next.events.length - MAX_LOG_EVENTS);
  }

  const data = raw.data ?? {};

  switch (raw.type) {
    case "feature_started": {
      const acceptance = Array.isArray(data["acceptance"])
        ? (data["acceptance"] as string[])
        : [];
      next.featureTitle =
        (typeof data["title"] === "string" && data["title"]) || slug;
      const summaryCandidate =
        typeof data["summary"] === "string"
          ? (data["summary"] as string).trim()
          : "";
      next.projectTitle =
        options.projectTitle ??
        (summaryCandidate.length > 0 ? summaryCandidate : next.projectTitle);
      next.outline = {};
      next.outlineOrder = [];
      next.pendingTests.clear();
      next.testFrozen.clear();
      next.orphans = [];
      acceptance.forEach((text, index) => {
        const id = `AC-${index + 1}`;
        const node = ensureOutlineNode(next, id, text);
        node.tests = [];
        node.pending = [];
      });
      next.coverage.specToTest.total = acceptance.length;
      next.coverage.specToTest.linked = 0;
      next.coverage.unit = { linked: 0, total: 0 };
      next.coverage.integration = { linked: 0, total: 0 };
      next.summaries = [];
      next.novelty = 0;
      break;
    }
    case "diff_summary": {
      const files = Array.isArray(data["files"])
        ? (data["files"] as Record<string, unknown>[])
        : [];
      const diffPath = options.diffFile;
      const diffLines = readDiffLines(diffPath);
      const explain: string[] = [];
      const fallbackId = next.outlineOrder[0] ?? "SC-0";
      let fallbackTitle = "Spec shard";
      if (next.outlineOrder.length > 0) {
        const firstKey = next.outlineOrder[0];
        if (firstKey) {
          const firstNode = next.outline[firstKey];
          if (firstNode) {
            fallbackTitle = firstNode.title;
          }
        }
      }
      const fallbackNode = ensureOutlineNode(next, fallbackId, fallbackTitle);
      files.forEach((entry) => {
        const pathValue =
          typeof entry["path"] === "string" ? entry["path"] : "spec shard";
        const addedTests = Array.isArray(entry["added_tests"])
          ? (entry["added_tests"] as unknown[]).map(normalizeTestName).filter(Boolean)
          : [];
        if (addedTests.length) {
          addedTests.forEach((test) => next.pendingTests.add(test));
          fallbackNode.pending = unique([
            ...fallbackNode.pending,
            ...addedTests,
          ]);
          explain.push(
            `Proposed ${addedTests.length} test(s) in ${path.basename(pathValue)}`,
          );
        }
      });
      const primaryFile = files.length > 0 ? files[0] : undefined;
      const primaryPath =
        primaryFile && typeof primaryFile["path"] === "string"
          ? (primaryFile["path"] as string)
          : "generator_patch.diff";
      next.lastDiff = {
        path: primaryPath,
        lines: diffLines,
        explain: explain.length ? explain.slice(0, 3) : ["Awaiting critic response"],
      };
      next.novelty = Math.min(100, next.pendingTests.size * 10);
      break;
    }
    case "spec_trace_update": {
      const coverage = (data["coverage"] ?? {}) as Record<string, unknown>;
      const entries = Array.isArray(coverage["entries"])
        ? (coverage["entries"] as Record<string, unknown>[])
        : [];
      let linked = 0;
      entries.forEach((entry) => {
        const index =
          typeof entry["index"] === "number"
            ? entry["index"]
            : parseInt(String(entry["index"] ?? 0), 10);
        if (!Number.isFinite(index) || index <= 0) {
          return;
        }
        const id = `AC-${index}`;
        const text =
          typeof entry["text"] === "string" && entry["text"]
            ? (entry["text"] as string)
            : id;
        const node = ensureOutlineNode(next, id, text);
        const tests = Array.isArray(entry["tests"])
          ? (entry["tests"] as unknown[]).map(normalizeTestName).filter(Boolean)
          : [];
        node.tests = unique(tests);
        node.pending = node.pending.filter((test) => !node.tests.includes(test));
        if (node.tests.length > 0) {
          linked += 1;
        }
        node.tests.forEach((test) => {
          next.testFrozen.add(test);
          next.pendingTests.delete(test);
        });
      });
      const missing = Array.isArray(coverage["missing"])
        ? (coverage["missing"] as Record<string, unknown>[])
        : [];
      missing.forEach((entry) => {
        const index =
          typeof entry["index"] === "number"
            ? entry["index"]
            : parseInt(String(entry["index"] ?? 0), 10);
        if (!Number.isFinite(index) || index <= 0) {
          return;
        }
        const id = `AC-${index}`;
        ensureOutlineNode(next, id, id);
      });
      const orphans = Array.isArray(coverage["orphans"])
        ? (coverage["orphans"] as unknown[]).map(normalizeTestName).filter(Boolean)
        : [];
      next.coverage.specToTest.linked = linked;
      next.orphans = unique(orphans);
      next.coverage.unit = {
        linked,
        total: Math.max(linked, next.coverage.unit.total),
      };
      break;
    }
    case "pytest_snapshot": {
      const status = typeof data["status"] === "string" ? data["status"] : "";
      if (status === "passed") {
        const linked = next.coverage.specToTest.linked;
        next.coverage.unit = { linked, total: linked };
      } else if (status === "failed") {
        next.coverage.unit = {
          linked: Math.max(0, next.coverage.specToTest.linked - 1),
          total: Math.max(1, next.coverage.specToTest.linked),
        };
      }
      break;
    }
    case "feature_completed": {
      next.summaries.push({
        short: "Generator completed",
        long: "Feature card marked complete by generator loop.",
      });
      break;
    }
    case "feature_failed": {
      const reason =
        typeof data["reason"] === "string" && data["reason"]
          ? (data["reason"] as string)
          : "Generator halted with failure";
      next.summaries.push({
        short: "Generator failed",
        long: reason,
      });
      break;
    }
    case "critic_guidance": {
      const guidance =
        typeof data["guidance"] === "string" ? data["guidance"].trim() : "";
      if (guidance) {
        next.summaries.push({
          short: "Critic guidance",
          long: guidance,
        });
      }
      break;
    }
    default:
      break;
  }

  return next;
}
