import { Link } from "react-router-dom";
import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import type { ReportInsight } from "@/api/types";

const EFFECT_CONFIG = {
  positive: { color: "text-status-good", bg: "bg-status-good/10", Icon: TrendingUp },
  negative: { color: "text-status-critical", bg: "bg-status-critical/10", Icon: TrendingDown },
  neutral: { color: "text-ink-muted", bg: "bg-surface-raised", Icon: Minus },
};

export function InsightCard({ insight }: { insight: ReportInsight }) {
  const config = EFFECT_CONFIG[insight.effect];
  const Icon = config.Icon;

  return (
    <Card>
      <CardBody className="space-y-2">
        <p className="text-sm font-semibold text-ink">{insight.title}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${config.color} ${config.bg}`}>
            <Icon className="size-3" />
            {insight.effect.toUpperCase()}
          </span>
          <span className="rounded-full bg-surface-raised px-2 py-0.5 font-mono text-ink-secondary">
            {insight.concerned_param}
          </span>
          <span className="text-ink-muted">confidence {insight.confidence}%</span>
        </div>
        {insight.recommended_action && (
          <p className="text-sm text-ink-secondary">🛠️ {insight.recommended_action}</p>
        )}
        <Link to={`/articles/${insight.article_id}`} className="block text-xs text-ink-muted hover:text-brand">
          Source: {insight.source_title}
        </Link>
      </CardBody>
    </Card>
  );
}
