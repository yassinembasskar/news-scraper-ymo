import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { useRetryExtraction, useSubmitManualContent } from "@/hooks/useArticles";

// Ported from the Streamlit app's _render_retry_button / _render_manual_content_form
// (frontend/components/article_list.py) — the two ways to recover a Failed
// article: retry automatic extraction, or paste the content by hand.
export function FailedExtractionActions({ articleId }: { articleId: string }) {
  const [showPasteForm, setShowPasteForm] = useState(false);
  const [content, setContent] = useState("");
  const retry = useRetryExtraction();
  const manual = useSubmitManualContent(articleId);

  const retryMessage = retry.isSuccess
    ? retry.data.status === "Successful"
      ? { tone: "good" as const, text: "Extraction succeeded." }
      : retry.data.status === "already_running"
        ? { tone: "warning" as const, text: retry.data.message }
        : retry.data.status === "not_found"
          ? { tone: "critical" as const, text: "Article no longer exists." }
          : { tone: "critical" as const, text: retry.data.message || "Extraction failed again." }
    : retry.isError
      ? { tone: "critical" as const, text: "Retry failed — try again in a moment." }
      : null;

  return (
    <div className="mt-2 flex flex-col gap-2 border-t border-border pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={() => retry.mutate(articleId)} loading={retry.isPending}>
          <RefreshCw className="size-3.5" />
          Retry automatic extraction
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowPasteForm((v) => !v)}>
          {showPasteForm ? "Cancel" : "Paste content manually"}
        </Button>
        {retryMessage && (
          <span
            className={cn(
              "text-xs",
              retryMessage.tone === "good" && "text-status-good",
              retryMessage.tone === "warning" && "text-status-warning",
              retryMessage.tone === "critical" && "text-status-critical",
            )}
          >
            {retryMessage.text}
          </span>
        )}
      </div>

      {showPasteForm && (
        <div className="flex flex-col gap-1.5">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Paste the article's content here…"
            rows={4}
            className="w-full rounded-lg border border-border bg-surface-raised p-2 text-xs text-ink placeholder:text-ink-muted"
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={!content.trim()}
              loading={manual.isPending}
              onClick={() => manual.mutate(content, { onSuccess: () => setShowPasteForm(false) })}
            >
              Save
            </Button>
            {manual.isError && <span className="text-xs text-status-critical">Could not save content.</span>}
          </div>
        </div>
      )}
    </div>
  );
}
