export type RawEvent = {
  ts: string;
  phase?: string;
  type: string;
  slug?: string | null;
  data?: Record<string, unknown>;
};

export type EventLogEntry = {
  ts: string;
  type: string;
  summary: string;
  phase?: string;
};

export type SummaryEntry = {
  short: string;
  long?: string;
};

export type PlannerTest = {
  id: string;
  question: string;
  measurement: string;
  context: string;
  status: string;
  component?: string;
  subcomponent?: string;
  tags: string[];
};

export type CodingStrategy = {
  testId: string;
  status?: string;
  strategy: string[];
  files: string[];
  notes?: string;
  lastUpdated?: string;
  source?: string;
};
