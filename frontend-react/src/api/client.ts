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
  DailyReportResponse,
  DisarchiveResponse,
  FlushArchivedResponse,
  GenerateStatusResponse,
  OverviewResponse,
  PipelineStatsResponse,
  RelativityFeedbackResponse,
  ScrapeStatusResponse,
  ScrapeSummaryResponse,
  SingleExtractionResponse,
  StageStatusResponse,
  StageTriggerResponse,
  TreeNodesResponse,
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

// ── Extraction ────────────────────────────────────────────────────────

export const triggerExtraction = () => post<StageTriggerResponse>("/articles/extract");
export const getExtractionStatus = () => get<StageStatusResponse>("/articles/extract/status");
export const stopExtraction = () => post<StageTriggerResponse>("/articles/extract/stop");
export const retryArticleExtraction = (articleId: string) =>
  post<SingleExtractionResponse>(`/articles/${articleId}/extract`);

// ── Title gate ────────────────────────────────────────────────────────

export const triggerTitleGate = () => post<StageTriggerResponse>("/articles/title-gate");
export const getTitleGateStatus = () => get<StageStatusResponse>("/articles/title-gate/status");
export const stopTitleGate = () => post<StageTriggerResponse>("/articles/title-gate/stop");

// ── Archived articles (title-gate rejects) ───────────────────────────

export const getArchivedArticles = () => get<Article[]>("/archived");
export const disarchiveArticle = (articleId: string) =>
  post<DisarchiveResponse>(`/archived/${articleId}/disarchive`);
export const flushArchivedArticles = () => post<FlushArchivedResponse>("/archived/flush");

// ── Embedding ─────────────────────────────────────────────────────────

export const triggerEmbedding = () => post<StageTriggerResponse>("/articles/embed");
export const getEmbeddingStatus = () => get<StageStatusResponse>("/articles/embed/status");
export const stopEmbedding = () => post<StageTriggerResponse>("/articles/embed/stop");

// ── Relativity scoring ────────────────────────────────────────────────

export const triggerScoring = () => post<StageTriggerResponse>("/articles/score");
export const getScoringStatus = () => get<StageStatusResponse>("/articles/score/status");
export const stopScoring = () => post<StageTriggerResponse>("/articles/score/stop");
export const getTreeNodes = () => get<TreeNodesResponse>("/tree/nodes");
export const submitRelativityFeedback = (articleId: string, direction: "more" | "less") =>
  post<RelativityFeedbackResponse>(`/articles/${articleId}/relativity-feedback`, { direction });
export const rescoreArticle = (articleId: string) =>
  post<RelativityFeedbackResponse>(`/articles/${articleId}/rescore`);

// ── Daily LLM Insight reports ────────────────────────────────────────

export const getReportCalendar = (year: number, month: number) =>
  get<CalendarResponse>(`/reports/calendar?year=${year}&month=${month}`);
export const getDailyReport = (reportDate: string) => get<DailyReportResponse>(`/reports/${reportDate}`);
export const generateDailyReport = (reportDate: string) =>
  post<StageTriggerResponse>(`/reports/${reportDate}/generate`);
export const getReportGenerateStatus = () => get<GenerateStatusResponse>("/reports/generate/status");
export const stopReportGeneration = () => post<StageTriggerResponse>("/reports/generate/stop");

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
export const getDayChart = (isoDate: string) => get<ChartResponse>(`/stats/chart/day?date=${isoDate}`);
export const getWeekChart = (isoYear: number, isoWeek: number) =>
  get<ChartResponse>(`/stats/chart/week?iso_year=${isoYear}&iso_week=${isoWeek}`);
export const getMonthChart = (year: number, month: number) =>
  get<ChartResponse>(`/stats/chart/month?year=${year}&month=${month}`);
export const getBucketSummary = (dateFrom: string, dateTo: string) =>
  get<BucketSummaryResponse>(`/stats/bucket-summary?date_from=${dateFrom}&date_to=${dateTo}`);
