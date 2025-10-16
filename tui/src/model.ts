import type { Event } from "./types";

export type OutlineNode = {
  title: string;
  tests: string[];
  covered: number;
  total: number;
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
  diff: string;
  explain: string[];
};

export type State = {
  projectTitle: string;
  featureTitle: string;
  outline: Record<string, OutlineNode>;
  testFrozen: Set<string>;
  coverage: CoverageState;
  checks: ChecksState;
  novelty: number;
  loop: "green" | "yellow" | "red";
  events: Event[];
  lastDiff?: DiffState;
};

export const initialState: State = {
  projectTitle: "Project Title",
  featureTitle: "Feature Card Title",
  outline: {},
  testFrozen: new Set<string>(),
  coverage: {
    specToTest: { linked: 0, total: 0 },
    unit: { pass: 0, total: 0 },
    integration: { pass: 0, total: 0 },
  },
  checks: { build: "skip" },
  novelty: 0,
  loop: "green",
  events: [],
};

export function reduce(state: State, event: Event): State {
  const next: State = {
    ...state,
    outline: { ...state.outline },
    testFrozen: new Set(state.testFrozen),
    coverage: {
      specToTest: { ...state.coverage.specToTest },
      unit: { ...state.coverage.unit },
      integration: { ...state.coverage.integration },
    },
    checks: { ...state.checks },
    events: [...state.events, event],
  };

  switch (event.type) {
    case "decompose.ok": {
      for (const id of event.details.created) {
        next.outline[id] ??= {
          title: id,
          tests: [],
          covered: 0,
          total: 0,
        };
      }
      return next;
    }
    case "test.proposed": {
      const node =
        next.outline[event.sub_id] ??
        (next.outline[event.sub_id] = {
          title: event.sub_id,
          tests: [],
          covered: 0,
          total: 0,
        });
      for (const test of event.tests) {
        if (!node.tests.includes(test.id)) {
          node.tests.push(test.id);
          node.total += 1;
          next.coverage.specToTest.total += 1;
        }
      }
      return next;
    }
    case "test.frozen": {
      for (const id of event.ids) {
        next.testFrozen.add(id);
        next.coverage.specToTest.linked += 1;
      }
      return next;
    }
    case "code.diff": {
      next.lastDiff = {
        path: event.path,
        diff: event.diff,
        explain: [...event.explain],
      };
      return next;
    }
    case "ci.result": {
      next.checks.lint = event.lint;
      next.checks.typecheck = event.typecheck;
      next.coverage.unit.pass += event.unit.pass;
      next.coverage.unit.total += event.unit.pass + event.unit.fail;
      return next;
    }
    case "loop.signal": {
      next.loop = event.level;
      return next;
    }
    case "summary.step":
    case "needs.human":
    default: {
      return next;
    }
  }
}
