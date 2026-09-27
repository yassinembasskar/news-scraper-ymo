import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { useArticle } from "@/hooks/useArticles";
import { displaySourceName } from "@/lib/format";

export default function ArticleDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: article, isLoading, error } = useArticle(id);

  if (isLoading) return <p className="text-sm text-ink-muted">Loading…</p>;
  if (error || !article) return <p className="text-sm text-status-critical">Could not load article.</p>;

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-ink-secondary hover:text-ink">
        <ArrowLeft className="size-4" />
        Back to dashboard
      </Link>

      <div>
        <h1 className="text-2xl font-semibold text-ink">{article.title}</h1>
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-sm text-ink-muted">
          <span className="uppercase">{article.language}</span>
          <span>·</span>
          <span>{new Date(article.published_at).toLocaleString()}</span>
          <span>·</span>
          <span>{displaySourceName(article.source_name, article.url)}</span>
          <a href={article.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-1 text-brand">
            Original article <ExternalLink className="size-3" />
          </a>
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <StatusBadge label="Extraction" status={article.ingestion_status} />
        </div>
        {article.matched_keywords.length > 0 && (
          <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
            <span>Keywords:</span>
            {article.matched_keywords.map((keyword) => (
              <span key={keyword} className="rounded-full bg-surface-raised px-2 py-0.5 text-ink-secondary">
                {keyword}
              </span>
            ))}
          </p>
        )}
      </div>

      <Card>
        <CardBody>
          <h2 className="mb-3 text-sm font-semibold text-ink">Scraped content</h2>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-secondary">
            {article.content || "No content available."}
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
