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
};

export type SummaryEntry = {
  short: string;
  long?: string;
};
