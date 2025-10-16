import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useInput } from "ink";
import chalk from "chalk";
import cliTruncate from "cli-truncate";
import stripAnsi from "strip-ansi";
import path from "path";
import { readFile } from "fs/promises";

import type { Event } from "./types";
import { initialState, reduce, type State } from "./model";

const DEFAULT_EVENTS_FILE = "events.ndjson";
const POLL_INTERVAL_MS = 600;

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

function formatEventSummary(event: Event): string {
  switch (event.type) {
    case "decompose.ok":
      return `${event.type} ${event.summary}`;
    case "test.proposed":
      return `${event.type} ${event.tests.length} tests`;
    case "test.frozen":
      return `${event.type} ${event.ids.length} frozen`;
    case "code.diff":
      return `${event.type} ${event.path}`;
    case "ci.result":
      return `${event.type} unit ${event.unit.pass}/${event.unit.pass + event.unit.fail}`;
    case "summary.step":
      return `${event.type} ${event.short}`;
    case "loop.signal":
      return `${event.type} ${event.level}`;
    case "needs.human":
      return `${event.type} ${event.reason}`;
    default:
      return event.type;
  }
}

function Outline({ state }: { state: State }) {
  const rows = useMemo(() => {
    const entries = Object.entries(state.outline).sort(([a], [b]) =>
      a.localeCompare(b),
    );
    return entries.map(([id, node]) => {
      const complete =
        node.total > 0 && node.covered >= node.total
          ? chalk.green("x")
          : chalk.dim(" ");
      const tests = node.total > 0 ? `${node.total}` : "-";
      const cov = `${node.covered}/${node.total}`;
      return ` [${complete}] ${id}: ${node.title}   Tests: ${tests}  Covered: ${cov}`;
    });
  }, [state.outline]);

  return (
    <Box flexDirection="column">
      <Text>
        {chalk.bold(`Project: ${state.projectTitle}`)}
      </Text>
      <Text>
        {chalk.bold(`Feature: ${state.featureTitle}`)}{" "}
        Status: {chalk.cyan("PLANNING ▸ TESTING ▸ CODING")}
      </Text>
      <Text> </Text>
      {rows.length === 0 ? (
        <Text dimColor>No decomposition events yet…</Text>
      ) : (
        rows.map((line, idx) => (
          <Text key={idx}>{cliTruncate(line, 60)}</Text>
        ))
      )}
    </Box>
  );
}

function Checks({ state }: { state: State }) {
  const spec = meter(
    "Spec→Test Map",
    state.coverage.specToTest.linked,
    state.coverage.specToTest.total,
  );
  const unit = meter("Unit", state.coverage.unit.pass, state.coverage.unit.total);
  const integration = meter(
    "Integration",
    state.coverage.integration.pass,
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
    </Box>
  );
}

function EventLog({ events }: { events: Event[] }) {
  const recent = events.slice(-6);
  return (
    <Box flexDirection="column">
      {recent.length === 0 ? (
        <Text dimColor>Waiting for events…</Text>
      ) : (
        recent.map((event, idx) => (
          <Text key={idx}>
            {chalk.dim(event.ts.slice(11, 19))} {formatEventSummary(event)}
          </Text>
        ))
      )}
    </Box>
  );
}

function TestsPane({ state }: { state: State }) {
  const rows: string[] = [];
  for (const [id, node] of Object.entries(state.outline)) {
    if (node.tests.length === 0) {
      continue;
    }
    rows.push(chalk.bold(id));
    for (const testId of node.tests) {
      const frozen = state.testFrozen.has(testId);
      rows.push(`  ${frozen ? chalk.green("✓") : chalk.dim("○")} ${testId}`);
    }
  }
  if (rows.length === 0) {
    rows.push(chalk.dim("No tests proposed yet…"));
  }
  return (
    <Box flexDirection="column">
      {rows.map((line, idx) => (
        <Text key={idx}>{cliTruncate(line, 74)}</Text>
      ))}
    </Box>
  );
}

function DiffPane({ state }: { state: State }) {
  if (!state.lastDiff) {
    return <Text dimColor>Waiting for diffs…</Text>;
  }
  const { path: diffPath, diff, explain } = state.lastDiff;
  const reasons =
    explain.length === 0 ? chalk.dim("No explanation") : explain.join(" · ");
  return (
    <Box flexDirection="column">
      <Text>{chalk.bold(`[DIFF] ${diffPath}`)}</Text>
      <Text>{cliTruncate(stripAnsi(diff), 74)}</Text>
      <Text>Why changed: {reasons}</Text>
    </Box>
  );
}

function ExplainPane({ state }: { state: State }) {
  const summary = state.events
    .filter((event): event is Extract<Event, { type: "summary.step" }> => event.type === "summary.step")
    .slice(-3);
  if (summary.length === 0) {
    return <Text dimColor>No step summaries yet…</Text>;
  }
  return (
    <Box flexDirection="column">
      {summary.map((event, idx) => (
        <Box key={idx} flexDirection="column" marginBottom={1}>
          <Text>{chalk.bold(event.short)}</Text>
          {event.long ? <Text>{event.long}</Text> : null}
        </Box>
      ))}
    </Box>
  );
}

function parseEvents(text: string): Event[] {
  const lines = text.split(/\r?\n/);
  const events: Event[] = [];
  for (const line of lines) {
    const candidate = line.trim();
    if (!candidate) {
      continue;
    }
    try {
      const parsed = JSON.parse(candidate) as Event;
      if (typeof parsed.ts === "string" && typeof parsed.type === "string") {
        events.push(parsed);
      }
    } catch {
      continue;
    }
  }
  return events;
}

type AppProps = {
  supportsInput: boolean;
};

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
  const eventsFile = useMemo(
    () =>
      process.env.TUI_EVENTS_FILE
        ? path.resolve(process.env.TUI_EVENTS_FILE)
        : path.resolve(process.cwd(), DEFAULT_EVENTS_FILE),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    let lineCount = 0;

    async function poll() {
      try {
        const text = await readFile(eventsFile, "utf-8");
        const lines = text.split(/\r?\n/).filter(Boolean);
        if (lines.length > lineCount) {
          const latestChunk = lines.slice(lineCount).join("\n");
          lineCount = lines.length;
          const events = parseEvents(latestChunk);
          if (!cancelled && events.length > 0) {
            setState((prev) => {
              let next = prev;
              for (const event of events) {
                next = reduce(next, event);
              }
              return next;
            });
          }
        } else if (lines.length < lineCount) {
          lineCount = lines.length;
        }
      } catch (error: unknown) {
        const err = error as NodeJS.ErrnoException;
        if (err.code === "ENOENT") {
          lineCount = 0;
        }
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [eventsFile]);

  return (
    <Box flexDirection="column">
      {supportsInput ? (
        <InputController
          onToggle={() =>
            setView((prev) =>
              prev === "diff"
                ? "tests"
                : prev === "tests"
                  ? "explain"
                  : "diff",
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
        <EventLog events={state.events} />
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
