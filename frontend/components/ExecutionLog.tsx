import { Terminal } from "lucide-react";
import type { LogEvent } from "@/lib/types";

export function ExecutionLog({ logs }: { logs: LogEvent[] }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-white/[.08] bg-[#0c0d10]">
      <div className="flex items-center gap-2 border-b border-white/[.1] px-5 py-4 text-sm font-semibold uppercase tracking-[.12em] text-zinc-200">
        <Terminal size={15} />
        Execution log
      </div>

      <div className="max-h-80 overflow-auto p-5 font-mono text-sm leading-7">
        {logs.length === 0 ? (
          <div className="text-zinc-300">
            Waiting for investigation events...
          </div>
        ) : (
          logs.map((entry, i) => (
            <div key={`${i}-${entry.log}`} className="flex gap-4">
              <span className="select-none text-zinc-400">
                {String(i + 1).padStart(2, "0")}
              </span>

              <span className="shrink-0 text-indigo-300">
                [{entry.current_stage || "system"}]
              </span>

              <span className="break-words text-zinc-200">{entry.log}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
