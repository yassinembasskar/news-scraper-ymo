import { useState } from "react";
import { ExternalLink, RotateCcw, Trash2 } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { useArchivedArticles, useDisarchiveArticle, useFlushArchivedArticles } from "@/hooks/useArchived";
import { displaySourceName } from "@/lib/format";

export default function Archived() {
  const { data: articles, isLoading } = useArchivedArticles();
  const disarchive = useDisarchiveArticle();
  const flush = useFlushArchivedArticles();
  const [confirmingFlush, setConfirmingFlush] = useState(false);

  const count = articles?.length ?? 0;

  const handleFlush = () => {
    flush.mutate(undefined, { onSuccess: () => setConfirmingFlush(false) });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-3xl font-bold uppercase tracking-wide text-ink">Archived</h1>
        <p className="text-sm text-ink-muted">
          Articles the title-gate LLM judged unable to affect the tracked business — never extracted, embedded, or
          scored. Disarchive one if the gate got it wrong, or flush the whole list to delete them permanently.
        </p>
      </div>

      <Card>
        <CardHeader
          title="Archived articles"
          subtitle={isLoading ? "Loading…" : `${count} article(s)`}
          action={
            count > 0 &&
            (confirmingFlush ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-secondary">Delete all {count} permanently?</span>
                <Button size="sm" variant="danger" onClick={handleFlush} loading={flush.isPending}>
                  Yes, flush
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmingFlush(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="danger" onClick={() => setConfirmingFlush(true)}>
                <Trash2 className="size-3.5" />
                Flush archived
              </Button>
            ))
          }
        />
        <div className="mt-4 divide-y divide-border">
          {articles?.map((article) => (
            <div key={article.id} className="flex items-center gap-4 px-5 py-3.5">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-1 text-sm font-medium text-ink">{article.title}</p>
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
              </div>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => disarchive.mutate(article.id)}
                loading={disarchive.isPending && disarchive.variables === article.id}
              >
                <RotateCcw className="size-3.5" />
                Disarchive
              </Button>
            </div>
          ))}
          {!isLoading && count === 0 && (
            <CardBody>
              <p className="text-center text-sm text-ink-muted">No archived articles.</p>
            </CardBody>
          )}
        </div>
      </Card>
    </div>
  );
}
