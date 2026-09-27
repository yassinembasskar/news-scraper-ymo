// Every HTTP call to the FastAPI backend lives here — components call
// these functions and get back typed data, never construct a URL or call
// fetch() themselves. Mirrors frontend/api_client.py's role in the
// Streamlit app: one centralized place to change if an endpoint's path
// or shape ever changes.

import type {
  Article,
  BucketSummaryResponse,
  CalendarResponse,
  ChartResponse,
  Cluster,
  DailyReportResponse,
  DayOverview,
  DisarchiveResponse,
  FlushArchivedResponse,
  OverviewResponse,
  PipelineStatsResponse,
  PreprocessingQualityResponse,
  PreprocessingStatusResponse,
  RunLogEntry,
  ScrapeStatusResponse,
  ScrapeSummaryResponse,
  SingleExtractionResponse,
  StageTriggerResponse,
  TitleGateStatusResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_BACKEND_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${response.status} ${body}`);
  }
  return response.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined });

// ── Health ────────────────────────────────────────────────────────────

export const getHealth = () => get<{ status: string; mongo_connected: boolean }>("/health");

// ── Scrape / update ──────────────────────────────────────────────────

export const triggerUpdate = () => post<ScrapeSummaryResponse>("/update");
export const getScrapeStatus = () => get<ScrapeStatusResponse>("/scrape-status");

// ── Preprocessing (clustering, extraction, summarization, entities) ────
// Extraction was folded into this combined stage on 2026-08-27 — it no
// longer runs as its own independent bulk stage, only as part of this
// job, scoped to clustered articles. The single-article retry below is
// unaffected — it's a per-article recovery action, not the bulk stage.

export const triggerPreprocessing = () => post<StageTriggerResponse>("/articles/preprocessing");
export const getPreprocessingStatus = () => get<PreprocessingStatusResponse>("/articles/preprocessing/status");
export const stopPreprocessing = () => post<StageTriggerResponse>("/articles/preprocessing/stop");
export const retryArticleExtraction = (articleId: string) =>
  post<SingleExtractionResponse>(`/articles/${articleId}/extract`);
export const getClusters = (date?: string) =>
  get<Cluster[]>(`/clusters${date ? `?date=${date}` : ""}`);
export const getDaysOverview = () => get<DayOverview[]>("/days-overview");

// ── Title gate ────────────────────────────────────────────────────────

export const triggerTitleGate = () => post<StageTriggerResponse>("/articles/title-gate");
export const getTitleGateStatus = () => get<TitleGateStatusResponse>("/articles/title-gate/status");
export const stopTitleGate = () => post<StageTriggerResponse>("/articles/title-gate/stop");

// ── Archived articles (title-gate rejects) ───────────────────────────

export const getArchivedArticles = () => get<Article[]>("/archived");
export const disarchiveArticle = (articleId: string) =>
  post<DisarchiveResponse>(`/archived/${articleId}/disarchive`);
export const flushArchivedArticles = () => post<FlushArchivedResponse>("/archived/flush");

// ── Daily LLM Insight reports (read-only — see types.ts) ────────────────

export const getReportCalendar = (year: number, month: number) =>
  get<CalendarResponse>(`/reports/calendar?year=${year}&month=${month}`);
export const getDailyReport = (reportDate: string) => get<DailyReportResponse>(`/reports/${reportDate}`);

// ── Articles ──────────────────────────────────────────────────────────

export interface ArticleFilters {
  language?: string;
  keyword?: string;
  date_from?: string;
  date_to?: string;
}

export const getArticles = (filters: ArticleFilters = {}) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return get<Article[]>(`/articles${query ? `?${query}` : ""}`);
};

export const getArticle = (articleId: string) => get<Article>(`/articles/${articleId}`);

export const submitManualContent = (articleId: string, content: string) =>
  put<{ status: string }>(`/articles/${articleId}/content`, { content });

// ── Stats ─────────────────────────────────────────────────────────────

export const getOverview = () => get<OverviewResponse>("/stats/overview");
export const getPipelineStats = () => get<PipelineStatsResponse>("/stats/pipeline");
export const getPreprocessingQuality = () => get<PreprocessingQualityResponse>("/stats/preprocessing-quality");
export const getDayChart = (isoDate: string) => get<ChartResponse>(`/stats/chart/day?date=${isoDate}`);
export const getWeekChart = (isoYear: number, isoWeek: number) =>
  get<ChartResponse>(`/stats/chart/week?iso_year=${isoYear}&iso_week=${isoWeek}`);
export const getMonthChart = (year: number, month: number) =>
  get<ChartResponse>(`/stats/chart/month?year=${year}&month=${month}`);
export const getBucketSummary = (dateFrom: string, dateTo: string) =>
  get<BucketSummaryResponse>(`/stats/bucket-summary?date_from=${dateFrom}&date_to=${dateTo}`);

// ── Logs (run-history across every stage) ───────────────────────────────

export const getLogs = (limit = 100) => get<RunLogEntry[]>(`/logs?limit=${limit}`);
