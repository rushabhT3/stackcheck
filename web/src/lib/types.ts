// API shapes. Dates and Decimals arrive as strings, mirroring docs/CONTRACTS.md C3.

export type Status = "OK" | "FLAG" | "CANT_VERIFY";
export type Severity = "info" | "low" | "medium" | "high" | "question";

export interface PageRef {
  doc_id: string;
  page: number;
}

export interface Evidence {
  kind: "quote" | "photo" | "number" | "computation";
  ref: PageRef | null;
  quote: string | null;
  image_id: string | null;
  overlay_path: string | null;
  value: string | null;
  note: string;
}

export interface Finding {
  finding_id: string;
  rule: string;
  status: Status;
  severity: Severity;
  quarter_end: string | null;
  block_label: string | null;
  title: string;
  explanation: string;
  evidence: Evidence[];
  inputs: Record<string, string>;
}

export interface StepStatus {
  name: string;
  state: "pending" | "running" | "done" | "failed" | "skipped";
  started_at: string | null;
  finished_at: string | null;
  note: string | null;
}

export interface CheckStatus {
  check_id: string;
  project_label: string;
  state: "queued" | "running" | "done" | "failed";
  steps: StepStatus[];
  progress: number;
  error: string | null;
  queue_position: number | null;
  created_at: string;
  updated_at: string;
}

export interface AgentStep {
  step: number;
  finding_id: string;
  action: string;
  argument: string | null;
  rationale: string;
  result: string;
}

export interface Verdict {
  headline: string;
  flags_high: number;
  flags_medium: number;
  flags_low: number;
  cant_verify: number;
  ok: number;
}

export interface CostSummary {
  usd_total: string;
  by_model: Record<string, string>;
  calls: number;
  cache_hits: number;
}

export interface PhotoFacts {
  image_id: string;
  kind: string;
  block_label_guess: string | null;
  stamp_text: string | null;
  stamp_date: string | null;
  vision_storeys_visible: number | null;
  top_obscured: boolean | null;
  confidence: string;
  reason: string;
}

export interface FloorCount {
  image_id: string;
  status: "OK" | "CANT_VERIFY";
  tier?: "CONFIDENT" | "ESTIMATE" | null;
  lower: number | null;
  upper: number | null;
  sam_rows: number | null;
  vision_estimate: number | null;
  reason: string;
  rows_y: number[] | null;
  building_box: number[] | null;
}

export interface PhotoRecord {
  image: { image_id: string };
  facts: PhotoFacts | null;
  dhash: string | null;
  floors: FloorCount | null;
  overlay_path: string | null;
}

export interface BlockFacts {
  block_label: string;
  plan_type: string | null;
  floors_above_ground_sanctioned: number | null;
  floors_entered_by_user: number | null;
  slabs_claimed_count: number | null;
  slabs_claimed_percent: number | null;
}

export interface ActivityFacts {
  name: string;
  percent_done: number | null;
}

export interface FinancialFacts {
  currency_unit: string | null;
  estimated_total_cost: string | null;
  cumulative_spent: string | null;
  cumulative_received: string | null;
  unit_overrides: Record<string, string>;
}

export interface QuarterEvidence {
  source: {
    kind?: string;
    value?: string;
    origin?: string;
    url?: string | null;
    filename?: string;
  };
  filing: {
    doc_id: string;
    project_label: string | null;
    quarter_end: string | null;
    blocks: BlockFacts[];
    activities: ActivityFacts[];
    financials: FinancialFacts;
    notes: string[];
  };
  photos: PhotoRecord[];
}

export interface VideoEvidence {
  video_id: string;
  block_label: string;
  tier: "CONFIDENT" | "ESTIMATE" | null;
  lower: number | null;
  upper: number | null;
  frames_used: number;
  frame_counts: number[];
  contact_sheet: string | null;
  uploaded_on: string | null;
  credit: string | null;
}

export interface Report {
  check_id: string;
  project_label: string;
  created_at: string;
  verdict: Verdict;
  findings: Finding[];
  evidence: {
    project_label: string;
    quarters: QuarterEvidence[];
    video: VideoEvidence | null;
    photos: PhotoRecord[];
    floors_above_ground: number | null;
  };
  trace: AgentStep[];
  summary_en: string;
  summary_hi: string | null;
  costs: CostSummary;
  versions: Record<string, string>;
  disclaimer: string;
}

export interface Sample {
  check_id: string;
  project_label: string;
  headline: string;
  constructed?: boolean;
}

export interface EvalValue {
  value: number | null;
  n: number;
  method: string;
}

export interface EvalSection {
  status: string;
  reason?: string;
}

export interface EvalReference {
  kind: string;
  note: string;
}

export interface EvalFloorSection extends EvalSection {
  n: number;
  exact: EvalValue;
  within1: EvalValue;
  abstain: EvalValue;
  wrong_when_confident: EvalValue;
  tiers?: { confident: EvalValue; estimate: EvalValue; cant_tell: EvalValue } | null;
  reference?: EvalReference;
  disagreements?: EvalValue | null;
}

export interface EvalExtractionSection extends EvalSection {
  n_docs: number;
  fields: Record<string, EvalValue>;
}

export interface EvalRulePrecision {
  true: number;
  false: number;
  unclear: number;
  precision: EvalValue;
}

export interface EvalFlagsSection extends EvalSection {
  n_rows: number;
  skipped_rows: number;
  rules: Record<string, EvalRulePrecision>;
  reviewer_note?: string | null;
  reviewed_on?: string | null;
}

export interface EvalBacktestProject {
  project_label: string;
  action_date: string;
  action_type: string;
  order_url: string;
  earliest_flag: string | null;
  quarters_before: EvalValue;
  status: string;
}

export interface EvalBacktestSection extends EvalSection {
  n_actions: number;
  skipped_rows: number;
  projects: EvalBacktestProject[];
}

export interface EvalVideoDetail {
  id: string;
  off_by: number;
  bench_low: number;
  bench_high: number;
  reference: number | [number, number];
  source: string | null;
}

export interface EvalVideoSection extends EvalSection {
  n: number;
  within1: EvalValue;
  videos?: EvalVideoDetail[];
}

export interface EvalGoldenSection extends EvalSection {
  activities_matched: number;
  activities_total: number;
  money_matched: number;
  money_total: number;
  known_gap: string;
  rule_pins: Record<string, string>;
  method: string;
}

export interface EvalReport {
  measured?: boolean;
  date: string | null;
  floors?: EvalFloorSection;
  floors_video?: EvalVideoSection;
  extraction?: EvalExtractionSection;
  golden?: EvalGoldenSection;
  flags?: EvalFlagsSection;
  backtest?: EvalBacktestSection;
}

export interface CheckSource {
  url?: string;
  upload_id?: string;
}
