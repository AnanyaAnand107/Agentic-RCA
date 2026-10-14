import type { OperationalMetrics } from "@/lib/api";

export function OperationalMetricsPanel({ metrics }: { metrics: OperationalMetrics | null }) {
  return <section className="panel p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="eyebrow">Operational visibility</div>
        <h2 className="mt-2 text-lg font-semibold text-white">Agent performance</h2>
      </div>
      <div className="text-sm text-zinc-300">{metrics?.total_agent_runs ?? 0} recorded stage runs</div>
    </div>
    {!metrics || Object.keys(metrics.agent_metrics).length === 0 ? <p className="mt-4 text-sm leading-6 text-zinc-300">Metrics appear here as investigations run. Latency and failure counts are persisted by the backend.</p> :
      <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[560px] text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-zinc-400"><tr><th className="pb-3 pr-4">Agent</th><th className="pb-3 pr-4">Runs</th><th className="pb-3 pr-4">Avg latency</th><th className="pb-3 pr-4">Failures</th><th className="pb-3 pr-4">Failure rate</th><th className="pb-3">Model usage</th></tr></thead>
        <tbody>{Object.entries(metrics.agent_metrics).map(([agent, value]) => <tr key={agent} className="border-t border-white/[.08] text-zinc-200"><td className="py-3 pr-4 capitalize">{agent.replaceAll("_", " ")}</td><td className="py-3 pr-4">{value.runs}</td><td className="py-3 pr-4">{Math.round(value.avg_latency_ms)} ms</td><td className="py-3 pr-4">{value.failures}</td><td className="py-3 pr-4">{(value.failure_rate * 100).toFixed(1)}%</td><td className="py-3 text-xs text-zinc-300">{Object.entries(value.models).map(([model, count]) => `${model} (${count})`).join(", ") || "Not reported"}</td></tr>)}</tbody>
      </table></div>}
  </section>;
}
