export type BaseEvent = {
  ts: string;
};

export type DecomposeOkEvent = BaseEvent & {
  type: "decompose.ok";
  feature_id: string;
  summary: string;
  details: {
    created: string[];
  };
};

export type TestProposedEvent = BaseEvent & {
  type: "test.proposed";
  sub_id: string;
  tests: {
    id: string;
    type: string;
    desc: string;
  }[];
};

export type TestFrozenEvent = BaseEvent & {
  type: "test.frozen";
  ids: string[];
  summary: string;
};

export type CodeDiffEvent = BaseEvent & {
  type: "code.diff";
  sub_id: string;
  path: string;
  diff: string;
  explain: string[];
};

export type CiResultEvent = BaseEvent & {
  type: "ci.result";
  run_id: string;
  unit: {
    pass: number;
    fail: number;
  };
  lint: "pass" | "fail";
  typecheck: "pass" | "fail";
};

export type SummaryStepEvent = BaseEvent & {
  type: "summary.step";
  short: string;
  long?: string;
};

export type LoopSignalEvent = BaseEvent & {
  type: "loop.signal";
  level: "green" | "yellow" | "red";
};

export type NeedsHumanEvent = BaseEvent & {
  type: "needs.human";
  reason: string;
};

export type Event =
  | DecomposeOkEvent
  | TestProposedEvent
  | TestFrozenEvent
  | CodeDiffEvent
  | CiResultEvent
  | SummaryStepEvent
  | LoopSignalEvent
  | NeedsHumanEvent;

export type NodeId = string;
