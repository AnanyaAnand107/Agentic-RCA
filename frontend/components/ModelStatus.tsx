import { Activity, CheckCircle2, ShieldCheck } from "lucide-react";
import type { Health } from "@/lib/api";

export function ModelStatus({ health }: { health: Health | null }) {
  if (!health) {
    return <div className="skeleton h-24 rounded-2xl" />;
  }

  return (
    <section className="grid gap-4 md:grid-cols-3">
      <StatusCard
        icon={<Activity size={17} />}
        label="Analysis service"
        value={health.models.gemini_configured ? "Ready" : "Not configured"}
        ready={health.models.gemini_configured}
        details={[`Gemini: ${health.models.gemini_model}`]}
        description={
          health.models.gemini_configured ? "Available" : "Check configuration"
        }
      />

      <StatusCard
        icon={<CheckCircle2 size={17} />}
        label="Code analysis"
        value={
          health.models.huggingface_configured ? "Ready" : "Not configured"
        }
        ready={health.models.huggingface_configured}
        details={[
          `Analysis: ${health.models.hf_analysis_model}`,
          `Code generation: ${health.models.hf_code_model}`,
        ]}
        description={
          health.models.huggingface_configured ? "Available" : "Check configuration"
        }
      />

      <StatusCard
        icon={<ShieldCheck size={17} />}
        label="Runtime verification"
        value={health.sandbox_enabled ? "Enabled" : "Deferred"}
        ready={health.sandbox_enabled}
      />
    </section>
  );
}

function StatusCard({
  icon,
  label,
  value,
  ready,
  description,
  details,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  ready: boolean;
  description?: string;
  details?: string[];
}) {
  return (
    <div className="rounded-2xl border border-white/[.08] bg-[#0f1014] p-5">
      <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[.1em] text-zinc-300">
        {icon}
        {label}
      </div>

      <div className="mt-3 text-base font-semibold text-zinc-200">{value}</div>

      {details?.map((detail) => (
        <div key={detail} className="mt-2 break-all text-xs text-zinc-400">
          {detail}
        </div>
      ))}

      {description && (
        <div
          className={`mt-3 text-sm ${
            ready ? "text-emerald-300" : "text-red-300"
          }`}
        >
          {description}
        </div>
      )}
    </div>
  );
}
