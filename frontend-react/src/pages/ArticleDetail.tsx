import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, Target } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { ScoreBadge } from "@/components/ScoreBadge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useArticle, useTreeNodes } from "@/hooks/useArticles";
import type { TreeNode, TreeNodesResponse } from "@/api/types";

function nodeLabel(nodeId: string, language: string, tree: TreeNodesResponse): string {
  const node = tree[nodeId];
  if (!node) return nodeId;
  return node.labels[language] ?? nodeId;
}

function nodePath(nodeId: string, language: string, tree: TreeNodesResponse): string {
  const labels: string[] = [];
  let current: string | null = nodeId;
  while (current !== null) {
    const node: TreeNode | undefined = tree[current];
    if (!node) break;
    labels.push(nodeLabel(current, language, tree));
    current = node.parent;
  }
  return labels.reverse().join(" › ");
}

export default function ArticleDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: article, isLoading, error } = useArticle(id);
  const { data: tree = {} } = useTreeNodes();

  if (isLoading) return <p className="text-sm text-ink-muted">Loading…</p>;
  if (error || !article) return <p className="text-sm text-status-critical">Could not load article.</p>;

  const language = (article.language || "en").toLowerCase();
  const explanation = article.score_explanation;
  const primaryNodeId = explanation?.primary_node?.node_id;
  const triggered = [...(explanation?.triggered_nodes ?? [])].sort(
    (a, b) => b.max_similarity - a.max_similarity,
  );

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
          <span>{article.source_name ?? "Unknown source"}</span>
          <a href={article.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-1 text-brand">
            Original article <ExternalLink className="size-3" />
          </a>
        </p>
        <div className="mt-3">
          <ScoreBadge score={article.relativity_score} scoredStatus={article.scored_status} className="text-sm" />
        </div>
      </div>

      <Card>
        <CardBody>
          <h2 className="mb-3 text-sm font-semibold text-ink">Scraped content</h2>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-secondary">
            {article.content || "No content available."}
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <h2 className="mb-3 text-sm font-semibold text-ink">Triggered relevance nodes</h2>
          {triggered.length === 0 ? (
            <p className="text-sm text-ink-muted">No relevance nodes were triggered for this article yet.</p>
          ) : (
            <div className="space-y-4">
              {triggered.map((node) => {
                const isPrimary = node.node_id === primaryNodeId;
                return (
                  <div key={node.node_id}>
                    <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-ink">
                      {isPrimary && <Target className="size-3.5 text-brand" />}
                      {nodePath(node.node_id, language, tree)}
                      {isPrimary && <span className="text-xs font-normal text-brand">primary match</span>}
                    </p>
                    <div className="flex items-center gap-3">
                      <ProgressBar value={Math.min(node.max_similarity, 1)} className="flex-1" />
                      <span className="w-16 shrink-0 text-xs text-ink-muted">
                        {node.max_similarity.toFixed(2)}
                      </span>
                      <span className="shrink-0 text-xs text-ink-muted">
                        {node.is_leaf ? "Leaf" : "Branch"} · {node.supporting_chunks.length} chunk(s)
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
