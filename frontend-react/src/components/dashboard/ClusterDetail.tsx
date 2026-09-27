// Opened by clicking a cluster in ClusterPanel. Shows the cluster's own
// condensed representation (deduped titles, entities — see
// services/cluster_condenser.py) plus every member article's extracted
// text, each collapsed by default since full article text can be long.

import { useState } from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { StatusBadge } from "@/components/StatusBadge";
import { cn } from "@/lib/utils";
import { useArticle } from "@/hooks/useArticles";
import { displaySourceName } from "@/lib/format";
import type { Cluster } from "@/api/types";

function ArticleRow({ articleId }: { articleId: string }) {
  const [expanded, setExpanded] = useState(false);
  const { data: article, isLoading } = useArticle(articleId);

  if (isLoading || !article) {
    return <div className="animate-pulse px-5 py-3.5 text-sm text-ink-muted">Loading article…</div>;
  }

  return (
    <div className="px-5 py-3.5">
      <button onClick={() => setExpanded((e) => !e)} className="flex w-full items-start gap-3 text-left">
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium text-ink">{article.title}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
            <span className="uppercase">{article.language}</span>
            <span>·</span>
            <span>{displaySourceName(article.source_name, article.url)}</span>
            <a
              href={article.url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="ml-1 inline-flex items-center text-ink-muted hover:text-brand"
            >
              <ExternalLink className="size-3" />
            </a>
          </p>
        </div>
        <StatusBadge label="Extraction" status={article.ingestion_status} />
      </button>
      {expanded && (
        <p className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-raised p-3 text-xs leading-relaxed text-ink-secondary">
          {article.content || "No content available."}
        </p>
      )}
    </div>
  );
}

const EXTRACTION_LABEL: Record<string, string> = {
  not_extracted: "Not extracted",
  extracting: "Extracting",
  extracted: "Extracted",
};

export function ClusterDetail({ cluster, onBack }: { cluster: Cluster; onBack: () => void }) {
  return (
    <Card>
      <CardHeader
        title={cluster.representative_title}
        subtitle={`${cluster.cluster_id} · ${cluster.article_ids.length} article(s) · ${EXTRACTION_LABEL[cluster.extraction_status]}`}
        action={
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-raised"
          >
            <ArrowLeft className="size-3.5" />
            Back to day
          </button>
        }
      />
      <CardBody className="space-y-4">
        <div>
          <p className="mb-1.5 text-xs font-semibold text-ink-muted">All titles in this cluster</p>
          <ul className="space-y-1 text-sm text-ink-secondary">
            {cluster.titles.map((title) => (
              <li key={title} className={cn(title === cluster.representative_title && "font-medium text-ink")}>
                {title}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="mb-1.5 text-xs font-semibold text-ink-muted">
            Entities {cluster.entities_extracted ? `(${cluster.entities.length})` : "(not extracted yet)"}
          </p>
          {cluster.entities.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {cluster.entities.map((entity) => (
                <span key={entity} className="rounded-full bg-surface-raised px-2 py-0.5 text-xs text-ink-secondary">
                  {entity}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-ink-muted">
              {cluster.entities_extracted ? "No entities found." : "Runs after condensing — not done yet for this cluster."}
            </p>
          )}
        </div>

        <div>
          <p className="mb-1.5 text-xs font-semibold text-ink-muted">
            Condensed content chunks {cluster.condensed ? `(${cluster.chunks.length})` : "(not condensed yet)"}
          </p>
          {cluster.chunks.length === 0 ? (
            <p className="text-sm text-ink-muted">
              {cluster.condensed ? "No extractable content was available for this cluster." : "Not condensed yet."}
            </p>
          ) : (
            <ul className="max-h-64 space-y-1.5 overflow-y-auto rounded-lg bg-surface-raised p-3 text-xs text-ink-secondary">
              {cluster.chunks.map((chunk, i) => (
                <li key={i}>{chunk}</li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="mb-1.5 text-xs font-semibold text-ink-muted">
            Insights {cluster.insights_generated ? `(${cluster.insights.length})` : "(not generated yet)"}
          </p>
          {cluster.insights.length > 0 ? (
            <ul className="space-y-2">
              {cluster.insights.map((insight, i) => (
                <li key={i} className="rounded-lg bg-surface-raised p-3 text-xs text-ink-secondary">
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium text-ink">{insight.title}</span>
                    <span className="shrink-0 uppercase text-ink-muted">{insight.effect}</span>
                  </div>
                  <p className="mt-1">{insight.recommended_action}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
                    <span className="rounded-full bg-surface px-1.5 py-0.5 font-mono">{insight.concerned_param}</span>
                    <span>confidence {insight.confidence}%</span>
                    {insight.chunk_indices.length > 0 && (
                      <span>{insight.chunk_indices.length} supporting chunk(s)</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">
              {cluster.insights_generated
                ? "Nothing operationally relevant found in this story."
                : "Runs after contextualization — not done yet for this cluster."}
            </p>
          )}
        </div>

        <div>
          <p className="mb-1.5 text-xs font-semibold text-ink-muted">
            Similar past clusters{" "}
            {cluster.contextualized ? `(${cluster.similar_clusters.length})` : "(not contextualized yet)"}
          </p>
          {cluster.similar_clusters.length === 0 ? (
            <p className="text-sm text-ink-muted">
              {cluster.contextualized
                ? "No earlier cluster shares any of this cluster's entities — likely a new story."
                : "Runs after entity extraction — not done yet for this cluster."}
            </p>
          ) : (
            <ul className="space-y-2">
              {cluster.similar_clusters.map((similar) => (
                <li
                  key={similar.cluster_id}
                  className="rounded-lg bg-surface-raised p-3 text-xs text-ink-secondary"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-medium text-ink">{similar.representative_title}</span>
                    <span className="shrink-0 text-ink-muted">{similar.score.toFixed(2)}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted">
                    <span>{similar.date}</span>
                    <span>·</span>
                    <span>{similar.cluster_id}</span>
                    {similar.shared_entities.map((entity) => (
                      <span key={entity} className="rounded-full bg-surface px-1.5 py-0.5">
                        {entity}
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardBody>

      <div>
        <p className="px-5 pb-1.5 text-xs font-semibold text-ink-muted">Articles</p>
        <div className="divide-y divide-border border-t border-border">
          {cluster.article_ids.map((id) => (
            <ArticleRow key={id} articleId={id} />
          ))}
        </div>
      </div>
    </Card>
  );
}
