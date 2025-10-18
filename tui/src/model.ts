import fs from "fs";
import path from "path";

import type {
  CodingStrategy,
  EventLogEntry,
  PlannerTest,
  RawEvent,
  SummaryEntry,
} from "./types.js";

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
  tests: Record<string, PlannerTest>;
  testOrder: string[];
  strategies: Record<string, CodingStrategy>;
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
  tests: {},
  testOrder: [],
  strategies: {},
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

type StrategyPayload = {
  id: string;
  status?: string;
  strategy: string[];
  files: string[];
  notes?: string;
  source?: string;
};

function cloneState(state: State): State {
  return {
    ...state,
    outline: { ...state.outline },
    outlineOrder: [...state.outlineOrder],
    tests: { ...state.tests },
    testOrder: [...state.testOrder],
    strategies: { ...state.strategies },
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
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function ensureQuestion(text: string, fallback: string): string {
  const trimmed = text.trim();
  if (!trimmed) {
    return fallback;
  }
  if (/[?？！]$/u.test(trimmed)) {
    return trimmed;
  }
  const withoutPunctuation = trimmed.replace(/[.!;:]+$/u, "").trim();
  if (!withoutPunctuation) {
    return fallback;
  }
  return `${withoutPunctuation}?`;
}

function normalizeTags(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const seen = new Set<string>();
  const result: string[] = [];
  value.forEach((item) => {
    const text = textValue(item);
    if (!text || seen.has(text.toLowerCase())) {
      return;
    }
    seen.add(text.toLowerCase());
    result.push(text);
  });
  return result;
}

function extractPlanTests(plan: unknown): PlannerTest[] {
  if (!plan || typeof plan !== "object") {
    return [];
  }
  const record = plan as Record<string, unknown>;
  const components = Array.isArray(record["components"])
    ? (record["components"] as unknown[])
    : [];
  const seen = new Set<string>();
  const results: PlannerTest[] = [];

  function addTest(
    raw: unknown,
    component: string,
    subcomponent: string | undefined,
    index: number,
  ): void {
    if (!raw || typeof raw !== "object") {
      return;
    }
    const data = raw as Record<string, unknown>;
    const candidateId =
      textValue(data["id"]) ||
      textValue(data["test_id"]) ||
      textValue(data["slug"]) ||
      textValue(data["name"]);
    const fallbackId = `${component}::${subcomponent ?? "component"}::${index + 1}`;
    const id = candidateId && !seen.has(candidateId) ? candidateId : fallbackId;
    if (seen.has(id)) {
      return;
    }
    seen.add(id);
    const questionRaw =
      textValue(data["question"]) ||
      textValue(data["title"]) ||
      textValue(data["name"]) ||
      fallbackId;
    const measurement =
      textValue(data["measurement"]) ||
      textValue(data["verification"]) ||
      textValue(data["how_to_verify"]) ||
      textValue(data["strategy"]);
    const context =
      textValue(data["context"]) || textValue(data["description"]) || "";
    const status = (textValue(data["status"]) || "proposed").toLowerCase();
    const entry: PlannerTest = {
      id,
      question: ensureQuestion(questionRaw, fallbackId),
      measurement,
      context,
      status,
      component,
      tags: normalizeTags(data["tags"]),
    };
    if (subcomponent) {
      entry.subcomponent = subcomponent;
    }
    results.push(entry);
  }

  function walkNode(node: unknown, component: string, sub?: string): void {
    if (!node || typeof node !== "object") {
      return;
    }
    const data = node as Record<string, unknown>;
    const tests = Array.isArray(data["tests"]) ? data["tests"] : [];
    tests.forEach((test, index) => addTest(test, component, sub, index));
    const subs = Array.isArray(data["subcomponents"]) ? data["subcomponents"] : [];
    subs.forEach((child, idx) => {
      if (!child || typeof child !== "object") {
        return;
      }
      const childData = child as Record<string, unknown>;
      const subName =
        textValue(childData["name"]) || `${component} :: Subcomponent ${idx + 1}`;
      walkNode(childData, component, subName);
    });
  }

  components.forEach((component, index) => {
    if (!component || typeof component !== "object") {
      return;
    }
    const compData = component as Record<string, unknown>;
    const compName =
      textValue(compData["name"]) || `Component ${index + 1}`;
    walkNode(compData, compName);
  });
  return results;
}

function ensureStrategyEntry(state: State, testId: string): CodingStrategy {
  const existing = state.strategies[testId];
  if (existing) {
    return existing;
  }
  const created: CodingStrategy = { testId, strategy: [], files: [] };
  state.strategies[testId] = created;
  return created;
}

function normalizeStrategySteps(value: unknown): string[] {
  const result: string[] = [];
  const seen = new Set<string>();

  function push(text: string): void {
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }
    const key = trimmed.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    result.push(trimmed);
  }

  function fromUnknown(input: unknown): void {
    if (!input) {
      return;
    }
    if (Array.isArray(input)) {
      input.forEach(fromUnknown);
      return;
    }
    if (typeof input === "string") {
      input.split(/\r?\n+/).forEach(push);
      return;
    }
    if (typeof input === "object") {
      const data = input as Record<string, unknown>;
      if (Array.isArray(data["steps"])) {
        fromUnknown(data["steps"]);
        return;
      }
      const summary =
        textValue(data["summary"]) ||
        textValue(data["plan"]) ||
        textValue(data["text"]) ||
        textValue(data["description"]);
      if (summary) {
        push(summary);
      }
    }
  }

  fromUnknown(value);
  return result;
}

function normalizeFiles(value: unknown): string[] {
  if (!value) {
    return [];
  }
  const seen = new Set<string>();
  const items: string[] = [];

  function push(candidate: string): void {
    const trimmed = candidate.trim();
    if (!trimmed) {
      return;
    }
    const key = trimmed.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    items.push(trimmed);
  }

  if (Array.isArray(value)) {
    value.forEach((item) => {
      if (typeof item === "string") {
        push(item);
      }
    });
    return items;
  }
  if (typeof value === "string") {
    value
      .split(/[\s,]+/)
      .map((token) => token.trim())
      .filter(Boolean)
      .forEach(push);
    return items;
  }
  if (typeof value === "object") {
    const data = value as Record<string, unknown>;
    return normalizeFiles(
      data["files"] ??
        data["file_paths"] ??
        data["paths"] ??
        data["targets"] ??
        data["touched_files"],
    );
  }
  return items;
}

function findTestId(record: Record<string, unknown>): string | undefined {
  const keys = [
    "test_id",
    "id",
    "test",
    "test_case",
    "name",
    "slug",
  ];
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function extractStrategyEntry(raw: unknown): StrategyPayload | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const data = raw as Record<string, unknown>;
  const id = findTestId(data);
  if (!id) {
    return undefined;
  }
  const strategy =
    normalizeStrategySteps(data["strategy"]) ||
    normalizeStrategySteps(data["strategies"]) ||
    normalizeStrategySteps(data["plan"]) ||
    normalizeStrategySteps(data["steps"]);
  const files = normalizeFiles(data);
  const status = textValue(data["status"]) || undefined;
  const notes =
    textValue(data["notes"]) ||
    textValue(data["reason"]) ||
    textValue(data["summary"]) ||
    undefined;
  const source = textValue(data["source"]) || undefined;
  const payload: StrategyPayload = {
    id,
    strategy,
    files,
  };
  if (status) {
    payload.status = status;
  }
  if (notes) {
    payload.notes = notes;
  }
  if (source) {
    payload.source = source;
  }
  return payload;
}

function extractStrategyEntriesFromPlan(plan: unknown): StrategyPayload[] {
  if (!plan || typeof plan !== "object") {
    return [];
  }
  const result: StrategyPayload[] = [];
  const record = plan as Record<string, unknown>;
  const components = Array.isArray(record["components"])
    ? (record["components"] as unknown[])
    : [];

  function walk(node: unknown): void {
    if (!node || typeof node !== "object") {
      return;
    }
    const data = node as Record<string, unknown>;
    const tests = Array.isArray(data["tests"]) ? data["tests"] : [];
    tests.forEach((test) => {
      const entry = extractStrategyEntry(test);
      if (entry) {
        result.push(entry);
      }
    });
    const subs = Array.isArray(data["subcomponents"])
      ? data["subcomponents"]
      : [];
    subs.forEach(walk);
  }

  components.forEach(walk);
  return result;
}

function extractStrategyEntriesFromData(
  data: Record<string, unknown> | undefined,
): StrategyPayload[] {
  if (!data) {
    return [];
  }
  const entries: StrategyPayload[] = [];
  const direct = extractStrategyEntry(data);
  if (direct) {
    entries.push(direct);
  }
  const candidateKeys = ["strategies", "tests", "entries", "updates"];
  candidateKeys.forEach((key) => {
    const value = data[key];
    if (!value) {
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => {
        const entry = extractStrategyEntry(item);
        if (entry) {
          entries.push(entry);
        }
      });
    } else if (typeof value === "object") {
      const entry = extractStrategyEntry(value);
      if (entry) {
        entries.push(entry);
      }
    }
  });
  const plan = data["plan"];
  if (plan && typeof plan === "object") {
    entries.push(...extractStrategyEntriesFromPlan(plan));
  }
  return entries;
}

function applyStrategyEntries(
  state: State,
  entries: StrategyPayload[],
  timestamp: string,
): void {
  entries.forEach((entry) => {
    const target = ensureStrategyEntry(state, entry.id);
    if (entry.strategy.length > 0) {
      target.strategy = entry.strategy;
    }
    if (entry.files.length > 0) {
      const existing = new Set(target.files ?? []);
      entry.files.forEach((file) => {
        if (!existing.has(file)) {
          existing.add(file);
        }
      });
      target.files = Array.from(existing);
    }
    if (entry.status) {
      target.status = entry.status;
    }
    if (entry.notes) {
      target.notes = entry.notes;
    }
    if (entry.source) {
      target.source = entry.source;
    }
    target.lastUpdated = timestamp;
  });
}

function summarise(raw: RawEvent): string {
  const data = raw.data ?? {};
  const phase = raw.phase ?? "generator";
  if (phase === "discriminator") {
    switch (raw.type) {
      case "run_started": {
        const mode = data["mode"] ?? "";
        const passNumber = data["pass_number"];
        const runId = data["run_id"];
        return `run_started ${mode ?? ""} pass=${passNumber ?? "?"} run=${runId ?? "?"}`;
      }
      case "stage_start": {
        const identifier = data["identifier"] ?? "";
        return `stage_start ${identifier ?? ""}`;
      }
      case "stage_end": {
        const identifier = data["identifier"] ?? "";
        const ok = data["ok"];
        return `stage_end ${identifier ?? ""} ${ok ? "PASS" : "FAIL"}`;
      }
      case "coverage_update": {
        const percent = data["percent"];
        return `coverage_update ${percent ?? "?"}%`;
      }
      case "mechanical_fixes": {
        const changed = data["changed"];
        return `mechanical_fixes ${changed ? "applied" : "skipped"}`;
      }
      case "llm_patch_decision": {
        const accepted = data["accepted"];
        const reason = data["reason"];
        return `llm_patch_decision ${accepted ? "accepted" : reason ?? "rejected"}`;
      }
      case "run_completed": {
        const ok = data["ok"];
        return `run_completed ${ok ? "PASS" : "FAIL"}`;
      }
      case "coding_strategy":
      case "coding_plan_snapshot":
      case "coding_status":
        return raw.type;
      default:
        return raw.type;
    }
  }
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
    case "critic_guidance": {
      const guidance =
        typeof data["guidance"] === "string" ? data["guidance"].trim() : "";
      if (guidance) {
        const snippet = guidance.length > 32 ? `${guidance.slice(0, 32)}…` : guidance;
        return `critic_guidance ${snippet}`;
      }
      return "critic_guidance";
    }
    case "component_plan_snapshot": {
      const plan = data["plan"];
      const total = extractPlanTests(plan).length;
      return `component_plan_snapshot tests=${total}`;
    }
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
  const eventSlug = typeof raw.slug === "string" ? raw.slug : undefined;
  const filterSlug = options.targetSlug;
  if (phase === "generator") {
    const slug = eventSlug ?? state.slug ?? filterSlug ?? undefined;
    if (!slug) {
      return state;
    }
    if (filterSlug && slug !== filterSlug) {
      return state;
    }
    const next = cloneState(state);
    next.slug = slug;
    if (options.projectTitle) {
      next.projectTitle = options.projectTitle;
    }
    const summary = summarise(raw);
    next.events.push({ ts: raw.ts, type: raw.type, summary, phase });
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
        next.tests = {};
        next.testOrder = [];
        next.strategies = {};
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
      case "component_plan_snapshot": {
        const plan = data["plan"];
        if (plan && typeof plan === "object") {
          const tests = extractPlanTests(plan);
          const mapping: Record<string, PlannerTest> = {};
          const order: string[] = [];
          tests.forEach((test) => {
            if (!mapping[test.id]) {
              mapping[test.id] = test;
              order.push(test.id);
              ensureStrategyEntry(next, test.id);
            }
          });
          next.tests = mapping;
          next.testOrder = order;
          Object.keys(next.strategies).forEach((key) => {
            if (!mapping[key]) {
              delete next.strategies[key];
            }
          });
        }
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

  if (phase === "discriminator") {
    if (filterSlug && eventSlug && eventSlug !== filterSlug) {
      return state;
    }
    const next = cloneState(state);
    if (options.projectTitle) {
      next.projectTitle = options.projectTitle;
    }
    const summary = summarise(raw);
    next.events.push({ ts: raw.ts, type: raw.type, summary, phase });
    if (next.events.length > MAX_LOG_EVENTS) {
      next.events = next.events.slice(next.events.length - MAX_LOG_EVENTS);
    }
    const data = (raw.data ?? {}) as Record<string, unknown>;
    if (
      raw.type === "coding_strategy" ||
      raw.type === "coding_plan_snapshot" ||
      raw.type === "coding_status"
    ) {
      const entries = extractStrategyEntriesFromData(data);
      if (entries.length > 0) {
        applyStrategyEntries(next, entries, raw.ts);
      }
    } else if (raw.type === "llm_patch_decision") {
      const accepted = data["accepted"];
      if (accepted === true) {
        Object.keys(next.strategies).forEach((key) => {
          const entry = next.strategies[key];
          if (!entry) {
            return;
          }
          entry.status = "completed";
          entry.lastUpdated = raw.ts;
        });
      }
    }
    return next;
  }

  return state;
}
