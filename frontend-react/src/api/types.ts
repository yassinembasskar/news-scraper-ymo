// Mirrors backend/app/models.py and every router's response models —
// one type per shape the backend actually returns. Kept intentionally
// close to the Pydantic source so a backend field rename is easy to spot.

export type IngestionStatus = "No Extraction" | "Pending" | "Successful" | "Failed" | "Expired" | "Archived";
export type EmbeddingStatus = "Not Embedded" | "Pending" | "Embedded" | "Failed" | "Expired";
export type ScoredStatus = "Not Scored" | "Pending" | "Scored" | "Failed";
export type TitleGateStatus = "Not Gated" | "Gated";

export interface ScoreExplanationNode {
  node_id: string;
  max_similarity: number;
  is_leaf: boolean;
  supporting_chunks: string[];
}

export interface ScoreExplanation {
  primary_node?: { node_id: string } | null;
  triggered_nodes?: ScoreExplanationNode[];
}

export interface Article {
  id: string;
  url: string;
  title: string;
  description: string | null;
  source_name: string | null;
  published_at: string;
  language: string;
  matched_keywords: string[];
  title_gate_status: TitleGateStatus;
  ingestion_status: IngestionStatus;
  content: string | null;
  chunk_ids: string[];
  number_of_chunks: number;
  embedding_status: EmbeddingStatus;
  relativity_score: number | null;
  scored_status: ScoredStatus;
  score_explanation: ScoreExplanation | null;
  relativity_score_adjustment: number;
  fetched_at: string;
}

// ── Stage trigger/status responses (extraction, embedding, scoring share
// this exact shape) ─────────────────────────────────────────────────────

export interface StageTriggerResponse {
  status: string; // "started" | "already_running" | "skipped" | "stopping" | "not_running"
  message: string;
}

export interface StageStatusResponse {
  in_progress: boolean;
  total: number;
  processed: number;
  estimated_minutes_remaining: number | null;
}

// ── Update / scrape ──────────────────────────────────────────────────────

export interface ScrapeSummaryResponse {
  status: string; // "success" | "partial" | "failed" | "skipped"
  message: string;
  date_from: string | null;
  date_to: string | null;
  new_articles: number;
  duplicate_articles: number;
  excluded_articles: number;
  malformed_articles: number;
  errors: string[];
}

export interface ScrapeStatusResponse {
  last_scraped: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
}

// ── Stats ──────────────────────────────────────────────────────────────

export interface OverviewResponse {
  today_total: number;
  today_top_keyword: string | null;
  month_total: number;
  month_top_keyword: string | null;
  last_scraped: string | null;
}

export interface TitleGateStageStats {
  total: number;
  gated: number;
  not_gated: number;
}

export interface ExtractionStageStats {
  total: number;
  successful: number;
  failed: number;
  archived: number;
  expired: number;
  not_extracted: number;
  pending: number;
}

export interface EmbeddingStageStats {
  total: number;
  embedded: number;
  not_embedded: number;
  failed: number;
  expired: number;
  pending: number;
}

export interface ScoringStageStats {
  total: number;
  scored: number;
  not_scored: number;
  failed: number;
  pending: number;
}

export interface PipelineStatsResponse {
  title_gate: TitleGateStageStats;
  extraction: ExtractionStageStats;
  embedding: EmbeddingStageStats;
  scoring: ScoringStageStats;
}

export interface ChartBucket {
  label: string;
  en: number;
  fr: number;
  ar: number;
  total: number;
  date_from: string;
  date_to: string;
}

export interface ChartResponse {
  range: string;
  period_label: string;
  buckets: ChartBucket[];
}

export interface BucketSummaryResponse {
  total: number;
  top_keyword: string | null;
  language_counts: Record<string, number>;
}

// ── Relativity scoring / tree ───────────────────────────────────────────

export interface RelativityFeedbackResponse {
  relativity_score: number;
  score_explanation: ScoreExplanation;
}

export interface TreeNode {
  parent: string | null;
  depth: number;
  labels: Record<string, string>;
}

export type TreeNodesResponse = Record<string, TreeNode>;

// ── Single-article extraction retry ─────────────────────────────────────

export interface SingleExtractionResponse {
  status: string; // "Successful" | "Failed" | "already_running" | "not_found"
  message: string;
}

// ── Daily LLM Insight reports ───────────────────────────────────────────

export type ReportRunStatus = "success" | "partial" | "failed" | "stopped";

export interface CalendarDay {
  report_date: string;
  candidate_count: number;
  has_report: boolean;
  relevant_count: number;
  articles_processed: number;
  run_status: ReportRunStatus;
}

export interface CalendarResponse {
  days: CalendarDay[];
  min_relativity_score: number;
}

export interface ReportInsight {
  article_id: string;
  source_title: string;
  title: string;
  confidence: number;
  concerned_param: string;
  effect: "positive" | "negative" | "neutral";
  recommended_action: string;
}

export interface DailyReportResponse {
  report_date: string;
  generated: boolean;
  generated_at: string | null;
  articles_processed: number;
  relevant_count: number;
  insights: ReportInsight[];
  run_status: ReportRunStatus;
  errors: string[];
}

export interface GenerateStatusResponse {
  in_progress: boolean;
  report_date: string | null;
  total: number;
  processed: number;
  estimated_minutes_remaining: number | null;
}

// ── Archived articles (title-gate LLM rejects) ──────────────────────────

export interface FlushArchivedResponse {
  deleted_count: number;
}

export interface DisarchiveResponse {
  status: string; // "ok" | "not_found"
}
