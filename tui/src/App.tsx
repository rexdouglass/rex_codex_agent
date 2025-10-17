import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useInput } from "ink";
import chalk from "chalk";
import cliTruncate from "cli-truncate";
import stripAnsi from "strip-ansi";
import path from "path";
import { readFile } from "fs/promises";

import type { RawEvent } from "./types.js";
import { initialState, reduce, type ReducerOptions, type State } from "./model.js";

const POLL_INTERVAL_MS = 700;

type AppProps = {
  supportsInput: boolean;
};

type ResolvedOptions = ReducerOptions & { eventsFile: string };

function meter(label: string, a: number, b: number): string {
  const total = Math.max(0, b);
  const current = Math.min(Math.max(a, 0), total);
  const ratio = total === 0 ? 0 : current / total;
  const width = 12;
  const filled = Math.round(ratio * width);
  const empty = width - filled;
  const bar =
    chalk.green("█".repeat(filled)) + chalk.dim("░".repeat(Math.max(0, empty)));
  return `${label}: ${current}/${total} ${bar}`;
}

function formatProjectTitle(state: State): string {
  return state.projectTitle ? state.projectTitle : "rex_codex_agent";
}

function Outline({ state }: { state: State }) {
  const rows = useMemo(() => {
    if (state.outlineOrder.length === 0) {
      return ["No decomposition events yet…"];
    }
    return state.outlineOrder.map((id: string) => {
      const node = state.outline[id];
      if (!node) {
        return ` [ ] ${id.toUpperCase()}`;
      }
      const linked = node.tests.length;
      const total = linked + node.pending.length;
      const status =
        linked > 0
          ? chalk.green("x")
          : node.pending.length > 0
            ? chalk.yellow("~")
            : chalk.dim(" ");
      const testsLabel =
        linked > 0
          ? `${linked} linked`
          : node.pending.length > 0
            ? `${node.pending.length} pending`
            : "0 planned";
      return ` [${status}] ${cliTruncate(node.title, 42)}   Tests: ${testsLabel}`;
    });
  }, [state.outlineOrder, state.outline]);

  return (
    <Box flexDirection="column">
      <Text>{chalk.bold(`Project: ${formatProjectTitle(state)}`)}</Text>
      <Text>
        {chalk.bold(`Feature: ${state.featureTitle}`)}{" "}
        Status: {chalk.cyan("PLANNING ▸ TESTING ▸ CODING")}
      </Text>
      <Text> </Text>
      {rows.map((line: string, idx: number) => (
        <Text key={idx}>
          {line.startsWith("[") || line.startsWith(" ")
            ? line
            : cliTruncate(line, 60)}
        </Text>
      ))}
    </Box>
  );
}

function Checks({ state }: { state: State }) {
  const spec = meter(
    "Spec→Test Map",
    state.coverage.specToTest.linked,
    state.coverage.specToTest.total,
  );
  const unit = meter(
    "Unit",
    state.coverage.unit.linked,
    state.coverage.unit.total,
  );
  const integration = meter(
    "Integration",
    state.coverage.integration.linked,
    state.coverage.integration.total,
  );
  const loopColour =
    state.loop === "green"
      ? chalk.green("green")
      : state.loop === "yellow"
        ? chalk.yellow("yellow")
        : chalk.red("red");

  return (
    <Box flexDirection="column">
      <Text>{spec}</Text>
      <Text>{unit}</Text>
      <Text>{integration}</Text>
      <Text>
        Lint: {state.checks.lint ?? "—"} ▪ Typecheck: {state.checks.typecheck ?? "—"} ▪ Build:{" "}
        {state.checks.build ?? "—"}
      </Text>
      <Text>
        Novelty: {state.novelty}% since last diff ▪ Loop Safeguard: {loopColour}
      </Text>
      {state.orphans.length > 0 ? (
        <Text dimColor>Orphan tests: {state.orphans.slice(0, 3).join(", ")}</Text>
      ) : null}
    </Box>
  );
}

function EventLog({ state }: { state: State }) {
  const recent = state.events.slice(-6);
  if (recent.length === 0) {
    return <Text dimColor>Waiting for events…</Text>;
  }
  return (
    <Box flexDirection="column">
      {recent.map((entry, idx: number) => (
        <Text key={idx}>
          {chalk.dim(entry.ts.slice(11, 19))} {entry.type} {entry.summary}
        </Text>
      ))}
    </Box>
  );
}

function TestsPane({ state }: { state: State }) {
  const rows: string[] = [];
  state.outlineOrder.forEach((id: string) => {
    const node = state.outline[id];
    if (!node) {
      return;
    }
    rows.push(chalk.bold(node.title));
    if (node.tests.length === 0 && node.pending.length === 0) {
      rows.push(chalk.dim("  (no tests linked yet)"));
    }
    node.tests.forEach((test: string) => {
      rows.push(`  ${chalk.green("✓")} ${test}`);
    });
    node.pending.forEach((test: string) => {
      rows.push(`  ${chalk.yellow("○")} ${test}`);
    });
  });
  if (state.pendingTests.size > 0) {
    rows.push(chalk.bold("Pending (unmapped)"));
    state.pendingTests.forEach((test: string) => {
      rows.push(`  ${chalk.yellow("○")} ${test}`);
    });
  }
  if (rows.length === 0) {
    rows.push(chalk.dim("No tests proposed yet…"));
  }
  return (
    <Box flexDirection="column">
      {rows.map((line: string, idx: number) => (
        <Text key={idx}>{cliTruncate(line, 74)}</Text>
      ))}
    </Box>
  );
}

function DiffPane({ state }: { state: State }) {
  if (!state.lastDiff) {
    return <Text dimColor>Waiting for diffs…</Text>;
  }
  const { path: diffPath, lines, explain } = state.lastDiff;
  return (
    <Box flexDirection="column">
      <Text>{chalk.bold(`[DIFF] ${diffPath}`)}</Text>
      {lines.length === 0 ? (
        <Text dimColor>Diff preview not available yet…</Text>
      ) : (
        lines.map((line: string, idx: number) => (
          <Text key={idx}>{cliTruncate(stripAnsi(line), 74)}</Text>
        ))
      )}
      <Text>
        Why changed:{" "}
        {explain.length > 0
          ? explain.map((item: string) => `• ${item}`).join("  ")
          : "pending critic summary"}
      </Text>
    </Box>
  );
}

function ExplainPane({ state }: { state: State }) {
  if (state.summaries.length === 0) {
    return <Text dimColor>No step summaries yet…</Text>;
  }
  return (
    <Box flexDirection="column">
      {state.summaries.slice(-3).map((entry, idx: number) => (
        <Box key={idx} flexDirection="column" marginBottom={1}>
          <Text>{chalk.bold(entry.short)}</Text>
          {entry.long ? <Text>{entry.long}</Text> : null}
        </Box>
      ))}
    </Box>
  );
}

function parseEvents(chunk: string): RawEvent[] {
  const lines = chunk.split(/\r?\n/);
  const events: RawEvent[] = [];
  for (const line of lines) {
    const candidate = line.trim();
    if (!candidate) {
      continue;
    }
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && typeof parsed["ts"] === "string") {
        events.push(parsed as RawEvent);
      }
    } catch {
      continue;
    }
  }
  return events;
}

function resolveOptions(): ResolvedOptions {
  const repoRoot =
    process.env.TUI_REPO_ROOT && process.env.TUI_REPO_ROOT.trim()
      ? path.resolve(process.env.TUI_REPO_ROOT.trim())
      : path.resolve(process.cwd(), "..");
  const diffFile =
    process.env.TUI_DIFF_FILE && process.env.TUI_DIFF_FILE.trim()
      ? path.resolve(process.env.TUI_DIFF_FILE.trim())
      : path.join(repoRoot, ".codex_ci", "generator_patch.diff");
  const slugEnv = process.env.TUI_SLUG?.trim();
  const targetSlug = slugEnv && slugEnv.length > 0 ? slugEnv : undefined;
  const titleEnv = process.env.TUI_PROJECT_TITLE?.trim();
  const projectTitle = titleEnv && titleEnv.length > 0 ? titleEnv : undefined;
  const eventsFile =
    process.env.TUI_EVENTS_FILE && process.env.TUI_EVENTS_FILE.trim()
      ? path.resolve(process.env.TUI_EVENTS_FILE.trim())
      : path.join(repoRoot, ".codex_ci", "events.jsonl");
  const resolved: ResolvedOptions = { diffFile, eventsFile };
  if (targetSlug) {
    resolved.targetSlug = targetSlug;
  }
  if (projectTitle) {
    resolved.projectTitle = projectTitle;
  }
  return resolved;
}

function InputController({ onToggle }: { onToggle: () => void }) {
  useInput((input) => {
    if (input.toLowerCase() === "t") {
      onToggle();
    }
  });
  return null;
}

export function App({ supportsInput }: AppProps) {
  const [state, setState] = useState<State>(initialState);
  const [view, setView] = useState<"diff" | "tests" | "explain">("diff");
  const { diffFile, targetSlug, projectTitle, eventsFile } = useMemo(
    () => resolveOptions(),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    let processedLines = 0;

    async function poll() {
      try {
        const text = await readFile(eventsFile, "utf-8");
        const lines = text.split(/\r?\n/);
        if (lines.length < processedLines) {
          processedLines = lines.length;
        }
        if (lines.length === processedLines) {
          return;
        }
        const chunk = lines.slice(processedLines).join("\n");
        processedLines = lines.length;
        const events = parseEvents(chunk);
        if (events.length === 0 || cancelled) {
          return;
        }
        setState((prev: State) => {
          let next = prev;
          const reducerOptions: ReducerOptions = {};
          if (diffFile) {
            reducerOptions.diffFile = diffFile;
          }
          if (targetSlug) {
            reducerOptions.targetSlug = targetSlug;
          }
          if (projectTitle) {
            reducerOptions.projectTitle = projectTitle;
          }
          events.forEach((event: RawEvent) => {
            next = reduce(next, event, reducerOptions);
          });
          return next;
        });
      } catch (error: unknown) {
        const err = error as NodeJS.ErrnoException;
        if (err.code === "ENOENT") {
          processedLines = 0;
        }
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [diffFile, eventsFile, projectTitle, targetSlug]);

  return (
    <Box flexDirection="column">
      {supportsInput ? (
        <InputController
          onToggle={() =>
            setView((prev) =>
              prev === "diff" ? "tests" : prev === "tests" ? "explain" : "diff",
            )
          }
        />
      ) : null}
      <Box
        borderStyle="round"
        borderColor="gray"
        paddingX={1}
        flexDirection="row"
      >
        <Box width={60}>
          <Outline state={state} />
        </Box>
        <Box width={38} borderStyle="single" borderColor="gray" paddingX={1}>
          <Checks state={state} />
        </Box>
      </Box>
      <Box borderStyle="single" borderColor="gray" paddingX={1}>
        <Text>{chalk.bold("Event Log")}</Text>
      </Box>
      <Box paddingX={1} paddingY={0}>
        <EventLog state={state} />
      </Box>
      <Box borderStyle="round" borderColor="gray" paddingX={1} paddingY={1}>
        {view === "diff" ? (
          <DiffPane state={state} />
        ) : view === "tests" ? (
          <TestsPane state={state} />
        ) : (
          <ExplainPane state={state} />
        )}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>
          {supportsInput
            ? `Controls: a=approve  r=request-rewrite  t=toggle view (current: ${view})`
            : `Controls (read-only terminal): view=${view} (press 't' when running in an interactive shell)`}
        </Text>
      </Box>
      <Box>
        <Text dimColor>Events file: {eventsFile}</Text>
      </Box>
    </Box>
  );
}
