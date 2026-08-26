import { useState } from "react";
import { StatCards } from "@/components/dashboard/StatCards";
import { UpdateButton } from "@/components/dashboard/UpdateButton";
import { PipelineStageCard } from "@/components/dashboard/PipelineStageCard";
import { ChartCard } from "@/components/dashboard/ChartCard";
import { ArticleList } from "@/components/dashboard/ArticleList";
import { useAnyStageRunning, usePipelineStats } from "@/hooks/usePipeline";
import type { ChartBucket } from "@/api/types";

export default function Dashboard() {
  const anyRunning = useAnyStageRunning();
  const { data: pipeline } = usePipelineStats(anyRunning);
  const [selectedBucket, setSelectedBucket] = useState<ChartBucket | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Dashboard</h1>
          <p className="text-sm text-ink-muted">Automotive-industry news, extraction, and relevance scoring.</p>
        </div>
        <UpdateButton />
      </div>

      <StatCards />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-4">
        <PipelineStageCard
          stage="titleGate"
          title="Title gate"
          ringColor="var(--color-brand)"
          completed={pipeline?.title_gate.gated ?? 0}
          total={pipeline?.title_gate.total ?? 0}
          breakdown={[{ label: "Not gated yet", value: pipeline?.title_gate.not_gated ?? 0 }]}
        />
        <PipelineStageCard
          stage="extraction"
          title="Extraction"
          ringColor="var(--color-series-en)"
          completed={pipeline?.extraction.successful ?? 0}
          total={pipeline?.extraction.total ?? 0}
          breakdown={[
            { label: "Failed", value: pipeline?.extraction.failed ?? 0 },
            { label: "Archived", value: pipeline?.extraction.archived ?? 0 },
            { label: "Expired", value: pipeline?.extraction.expired ?? 0 },
            { label: "Not extracted", value: pipeline?.extraction.not_extracted ?? 0 },
          ]}
        />
        <PipelineStageCard
          stage="embedding"
          title="Embedding"
          ringColor="var(--color-series-fr)"
          completed={pipeline?.embedding.embedded ?? 0}
          total={pipeline?.embedding.total ?? 0}
          breakdown={[
            { label: "Failed", value: pipeline?.embedding.failed ?? 0 },
            { label: "Not embedded", value: pipeline?.embedding.not_embedded ?? 0 },
          ]}
        />
        <PipelineStageCard
          stage="scoring"
          title="Relativity scoring"
          ringColor="var(--color-series-ar)"
          completed={pipeline?.scoring.scored ?? 0}
          total={pipeline?.scoring.total ?? 0}
          breakdown={[
            { label: "Failed", value: pipeline?.scoring.failed ?? 0 },
            { label: "Not scored", value: pipeline?.scoring.not_scored ?? 0 },
          ]}
        />
      </div>

      <ChartCard onSelectBucket={setSelectedBucket} />

      <ArticleList selectedBucket={selectedBucket} onClearBucket={() => setSelectedBucket(null)} />
    </div>
  );
}
