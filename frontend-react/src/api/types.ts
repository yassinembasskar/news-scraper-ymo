// Mirrors backend/app/models.py and every router's response models —
// one type per shape the backend actually returns. Kept intentionally
// close to the Pydantic source so a backend field rename is easy to spot.

export type IngestionStatus = "No Extraction" | "Pending" | "Successful" | "Failed" | "Archived";
export type TitleGateStatus = "Not Gated" | "Gated";

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
  fetched_at: string;
}

// ── Stage trigger/status responses (extraction, title-gate share this
// exact shape) ──────────────────────────────────────────────────────────

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

// Title gate works one article at a time, oldest-published-first, but
// this day-level view (days_gated/days_total/current_day) is what's
// actually meaningful to show — always freshly computed from current DB
// state, independent of whether a run is active.
export interface TitleGateStatusResponse extends StageStatusResponse {
  days_gated: number;
  days_total: number;
  current_day: string | null;
  gated_total: number;
  archived_total: number;
  articles_total: number;
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
  not_extracted: number;
  pending: number;
}

export interface PipelineStatsResponse {
  title_gate: TitleGateStageStats;
  extraction: ExtractionStageStats;
}

// ── Preprocessing quality (watched live during a backlog run) ──────────
// Each phase's numbers are only over clusters that have actually reached
// that phase — clusters_processed distinguishes "no data yet" (still
// early in a run) from "processed, and the rate/average genuinely is
// this" (see backend/app/repository.py's get_preprocessing_quality_metrics).

export interface CondensingQuality {
  clusters_processed: number;
  avg_chunks_per_cluster: number | null;
  avg_titles_per_cluster: number | null;
  zero_chunk_clusters: number;
}

export interface EntityQuality {
  clusters_processed: number;
  avg_entities_per_cluster: number | null;
  total_canonical_entities: number;
}

export interface ContextualizationQuality {
  clusters_processed: number;
  hit_rate_pct: number | null;
  avg_top_score: number | null;
}

export interface InsightQuality {
  clusters_processed: number;
  yield_rate_pct: number | null;
  total_insights: number;
  avg_confidence: number | null;
  avg_chunk_citations: number | null;
}

export interface PreprocessingQualityResponse {
  condensing: CondensingQuality;
  entities: EntityQuality;
  contextualization: ContextualizationQuality;
  insights: InsightQuality;
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

// ── Single-article extraction retry ─────────────────────────────────────

export interface SingleExtractionResponse {
  status: string; // "Successful" | "Failed" | "already_running" | "not_found"
  message: string;
}

// ── Daily LLM Insight reports ───────────────────────────────────────────
// Read-only now — insight generation is preprocessing's own phase 6 (see
// PreprocessingPhase below), not an on-demand per-day job, so there's no
// "generate"/"stop" trigger here anymore, just a calendar/day view over
// what preprocessing has already produced for the clusters collection.

export interface CalendarDay {
  report_date: string;
  total_clusters: number;
  insights_generated_clusters: number;
  relevant_count: number;
}

export interface CalendarResponse {
  days: CalendarDay[];
}

export interface DailyInsightItem {
  cluster_id: string;
  representative_title: string;
  title: string;
  confidence: number;
  concerned_param: string;
  effect: "positive" | "negative" | "neutral";
  recommended_action: string;
  chunk_indices: number[];
}

export interface DailyReportResponse {
  report_date: string;
  total_clusters: number;
  insights_generated_clusters: number;
  pending_clusters: number;
  insights: DailyInsightItem[];
}

// ── Preprocessing (clustering, extraction, condensing, entities,
// contextualization, insights) ──────────────────────────────────────────
// Phase-aware, unlike the other stages' StageStatusResponse: `phase`
// names whichever phase is currently running (or just finished), and
// phase_total/phase_processed are scoped to that phase alone, not
// blended across all six (see backend/app/models.py's ClusteringRunStatus
// docstring for why). The *_done counters are cumulative for the whole
// run, so an earlier-finished phase still shows its final count.

export type PreprocessingPhase =
  | "clustering"
  | "extraction"
  | "condensing"
  | "entities"
  | "contextualization"
  | "insights";

export interface PreprocessingStatusResponse {
  in_progress: boolean;
  total: number;
  processed: number;
  estimated_minutes_remaining: number | null;
  phase: PreprocessingPhase | null;
  phase_total: number;
  phase_processed: number;
  days_done: number;
  articles_extracted_done: number;
  clusters_condensed_done: number;
  clusters_entities_done: number;
  clusters_contextualized_done: number;
  clusters_insights_done: number;
  // All-time day coverage — distinct from days_done above, which counts
  // only days clustered *in this run*.
  days_clustered: number;
  days_total: number;
}

export type ClusterExtractionStatus = "not_extracted" | "extracting" | "extracted";

// One past cluster linked during contextualization (phase 5,
// backend/app/services/contextualization.py) — score = (chunk-embedding
// centroid similarity) x (shared real canonicalized entity count).
export interface SimilarClusterRef {
  cluster_id: string;
  date: string;
  representative_title: string;
  score: number;
  shared_entities: string[];
}

// One insight the LLM found in a cluster's chunks (phase 6, backend/app/
// services/llm_insight.assess_cluster) — chunk_indices point into this
// same cluster's `chunks` array (0-based), the chunks that support it.
export interface ClusterInsight {
  title: string;
  confidence: number;
  concerned_param: string;
  effect: "positive" | "negative" | "neutral";
  recommended_action: string;
  chunk_indices: number[];
}

// titles/chunks are extractive (embedding-based dedup + MMR diversity
// selection, backend/app/services/cluster_condenser.py), not an LLM
// summary — see that module's docstring. representative_title/
// extraction_status are computed on read (repository.get_cluster_display_info),
// not stored on the document.
export interface Cluster {
  cluster_id: string;
  date: string;
  article_ids: string[];
  titles: string[];
  chunks: string[];
  condensed: boolean;
  entities: string[];
  entities_extracted: boolean;
  similar_clusters: SimilarClusterRef[];
  contextualized: boolean;
  insights: ClusterInsight[];
  insights_generated: boolean;
  representative_title: string;
  extraction_status: ClusterExtractionStatus;
}

// One row per UTC calendar day with at least one article — the
// day-by-day cluster browser's data source (GET /days-overview). Days
// with zero articles don't appear here; the frontend fills the gaps
// itself to render them as empty cells in a continuous timeline.
export interface DayOverview {
  date: string;
  total: number;
  gated: number;
  archived: number;
  passed: number;
  is_gate_complete: boolean;
  extracted_successful: number;
  extraction_attempted: number;
  extraction_success_rate: number | null;
  cluster_count: number;
  is_clustered: boolean;
}

// ── Logs (run-history across every stage) ───────────────────────────────

export type PipelineLogStage = "scrape" | "title_gate" | "preprocessing";

// phase_durations is only ever populated for "preprocessing" entries
// (clustering/extraction/condensing/entities/contextualization/insights)
// — every other stage is single-phase, so duration_seconds alone already
// says it all. There's no separate "report" stage — insight generation
// is preprocessing's own phase 6.
export interface RunLogEntry {
  stage: PipelineLogStage;
  status: string;
  message: string;
  duration_seconds: number | null;
  phase_durations: Record<string, number> | null;
  logged_at: string;
}

// ── Archived articles (title-gate LLM rejects) ──────────────────────────

export interface FlushArchivedResponse {
  deleted_count: number;
}

export interface DisarchiveResponse {
  status: string; // "ok" | "not_found"
}
