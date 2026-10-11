import { Activity, Check, Circle } from "lucide-react";
import type { AgentKey } from "@/lib/types";

const agents: {
  key: AgentKey;
  label: string;
  detail: string;
}[] = [
  {
    key: "telemetry_parser",
    label: "Telemetry Parser",
    detail: "Extract incident facts",
  },
  {
    key: "git_forensic",
    label: "Git Forensics",
    detail: "Correlate repository history",
  },
  {
    key: "reproduction",
    label: "Reproduction",
    detail: "Generate a grounded test",
  },
  {
    key: "patch_synthesizer",
    label: "Patch Synthesis",
    detail: "Generate candidate fix",
  },
  {
    key: "verification",
    label: "Validation",
    detail: "Run structural checks",
  },
];

export function AgentPipeline({
  activeStage,
  complete,
}: {
  activeStage?: string;
  complete: boolean;
}) {
  const index = agents.findIndex((agent) => agent.key === activeStage);

  return (
    <section className="grid gap-3 lg:grid-cols-5">
      {agents.map((agent, i) => {
        const status =
          complete || (index >= 0 && i < index)
            ? "done"
            : index === i
              ? "running"
              : "queued";

        return (
          <div key={agent.key} className={`agent-card ${status}`}>
            <div className="flex items-start gap-3">
              <div className="mt-0.5 shrink-0">
                {status === "done" ? (
                  <Check size={17} />
                ) : status === "running" ? (
                  <Activity size={17} className="animate-pulse" />
                ) : (
                  <Circle size={15} />
                )}
              </div>

              <div className="min-w-0">
                <div className="text-base font-semibold text-zinc-100">
                  {agent.label}
                </div>

                <div className="mt-1.5 text-sm leading-6 text-zinc-300">
                  {agent.detail}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}
