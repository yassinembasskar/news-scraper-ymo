import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useTriggerUpdate } from "@/hooks/usePipeline";

export function UpdateButton() {
  const trigger = useTriggerUpdate();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleClick = () => {
    setResult(null);
    trigger.mutate(undefined, {
      onSuccess: (data) => setResult({ ok: data.status !== "failed", message: data.message }),
      onError: (error) => setResult({ ok: false, message: (error as Error).message }),
    });
  };

  return (
    <div className="flex items-center gap-3">
      {result && (
        <span className={`text-xs ${result.ok ? "text-status-good" : "text-status-critical"}`}>
          {result.message}
        </span>
      )}
      <Button variant="secondary" onClick={handleClick} loading={trigger.isPending}>
        <RefreshCw className="size-4" />
        Update now
      </Button>
    </div>
  );
}
