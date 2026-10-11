export type AgentKey =
  | "telemetry_parser"
  | "git_forensic"
  | "reproduction"
  | "patch_synthesizer"
  | "verification";

export type LogEvent = {
  event?: string;
  current_stage?: string;
  progress_pct?: number;
  log: string;
};
