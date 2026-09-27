import { useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, Search, X } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { FailedExtractionActions } from "./FailedExtractionActions";
import { useArticles } from "@/hooks/useArticles";
import { displaySourceName } from "@/lib/format";
import type { ChartBucket } from "@/api/types";

const LANGUAGES = [
  { value: "", label: "All languages" },
  { value: "en", label: "English" },
  { value: "fr", label: "French" },
  { value: "ar", label: "Arabic" },
];

export function ArticleList({
  selectedBucket,
  onClearBucket,
}: {
  selectedBucket: ChartBucket | null;
  onClearBucket: () => void;
}) {
  const [language, setLanguage] = useState("");
  const [keyword, setKeyword] = useState("");

  const { data: articles, isLoading } = useArticles({
    language: language || undefined,
    keyword: keyword || undefined,
    date_from: selectedBucket?.date_from,
    date_to: selectedBucket?.date_to,
  });

  const sorted = articles ?? [];

  return (
    <Card>
      <CardHeader
        title="Articles"
        subtitle={isLoading ? "Loading…" : `${sorted.length} article(s)`}
      />
      <div className="flex flex-wrap items-center gap-2 px-5 pt-4">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-muted" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="Search title or content…"
            className="w-64 rounded-lg border border-border bg-surface-raised py-1.5 pl-8 pr-3 text-sm text-ink placeholder:text-ink-muted"
          />
        </div>
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="rounded-lg border border-border bg-surface-raised px-2.5 py-1.5 text-sm text-ink"
        >
          {LANGUAGES.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
        {selectedBucket && (
          <button
            onClick={onClearBucket}
            className="flex items-center gap-1 rounded-full bg-brand/10 px-2.5 py-1 text-xs font-medium text-brand"
          >
            {selectedBucket.label}
            <X className="size-3" />
          </button>
        )}
      </div>

      <div className="mt-4 divide-y divide-border">
        {sorted.map((article) => (
          <div key={article.id} className="px-5 py-3.5">
            <div className="flex items-center gap-4">
              <div className="min-w-0 flex-1">
                <Link
                  to={`/articles/${article.id}`}
                  className="line-clamp-1 text-sm font-medium text-ink hover:text-brand"
                >
                  {article.title}
                </Link>
                <p className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-muted">
                  <span className="uppercase">{article.language}</span>
                  <span>·</span>
                  <span>{displaySourceName(article.source_name, article.url)}</span>
                  <span>·</span>
                  <span>{new Date(article.published_at).toLocaleDateString()}</span>
                  <a
                    href={article.url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-1 inline-flex items-center text-ink-muted hover:text-brand"
                  >
                    <ExternalLink className="size-3" />
                  </a>
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <StatusBadge label="Extraction" status={article.ingestion_status} />
                </div>
              </div>
            </div>
            {article.ingestion_status === "Failed" && <FailedExtractionActions articleId={article.id} />}
          </div>
        ))}
        {!isLoading && sorted.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-ink-muted">No articles match these filters.</p>
        )}
      </div>
    </Card>
  );
}
