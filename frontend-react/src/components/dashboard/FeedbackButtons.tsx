import { RotateCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useRelativityFeedback, useRescoreArticle } from "@/hooks/useArticles";
import type { ScoredStatus } from "@/api/types";

export function FeedbackButtons({ articleId, scoredStatus }: { articleId: string; scoredStatus: ScoredStatus }) {
  const feedback = useRelativityFeedback(articleId);
  const rescore = useRescoreArticle(articleId);

  if (scoredStatus !== "Scored") return null;

  return (
    <div className="flex items-center gap-1">
      <Button
        size="sm"
        variant="ghost"
        title="More relative"
        onClick={() => feedback.mutate("more")}
        loading={feedback.isPending && feedback.variables === "more"}
        className="px-2"
      >
        <ThumbsUp className="size-3.5" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        title="Less relative"
        onClick={() => feedback.mutate("less")}
        loading={feedback.isPending && feedback.variables === "less"}
        className="px-2"
      >
        <ThumbsDown className="size-3.5" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        title="Rescore"
        onClick={() => rescore.mutate()}
        loading={rescore.isPending}
        className="px-2"
      >
        <RotateCw className="size-3.5" />
      </Button>
    </div>
  );
}
