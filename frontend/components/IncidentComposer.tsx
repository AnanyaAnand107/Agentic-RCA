import { FileArchive, Upload, X } from "lucide-react";

type Props = {
  description: string;
  source: string;
  repo: File | null;
  onDescription: (value: string) => void;
  onSource: (value: string) => void;
  onRepo: (value: File | null) => void;
  onClearRepo: () => void;
};

export function IncidentComposer({
  description,
  source,
  repo,
  onDescription,
  onSource,
  onRepo,
  onClearRepo,
}: Props) {
  return (
    <div className="grid gap-5 lg:grid-cols-[1.35fr_.65fr]">
      <div className="panel p-6">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="eyebrow">01 / Incident</div>

            <h2 className="section-title">Production telemetry</h2>
          </div>

          <select
            value={source}
            onChange={(e) => onSource(e.target.value)}
            className="select"
            aria-label="Telemetry source"
          >
            <option value="sentry">Sentry</option>
            <option value="datadog">Datadog</option>
            <option value="prometheus">Prometheus</option>
            <option value="pagerduty">PagerDuty</option>
            <option value="other">Other</option>
          </select>
        </div>

        <textarea
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          maxLength={20000}
          placeholder="Paste the stack trace, alert payload, exception, or incident context..."
          className="textarea min-h-[280px]"
        />

        <div className="mt-3 flex items-center justify-between gap-4 text-sm text-zinc-300">
          <span>
            Provide the original error and stack trace when available.
          </span>

          <span>
            {description.length.toLocaleString()}
            /20,000
          </span>
        </div>
      </div>

      <div className="panel p-6">
        <div className="eyebrow">02 / Repository</div>

        <h2 className="section-title">Git history</h2>

        <label className="mt-6 flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-white/[.1] bg-white/[.012] px-6 text-center transition hover:border-indigo-500/40 hover:bg-indigo-500/[.02]">
          <Upload size={23} className="mb-4 text-zinc-300" />

          <span className="text-base font-medium text-zinc-200">
            Upload repository ZIP
          </span>

          <span className="mt-2 max-w-xs text-base leading-7 text-zinc-300">
            Include the .git directory when commit-level analysis is required.
          </span>

          <input
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(e) => onRepo(e.target.files?.[0] || null)}
          />
        </label>

        {repo && (
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-emerald-500/15 bg-emerald-500/[.035] p-4 text-sm text-emerald-300">
            <FileArchive size={17} />

            <span className="flex-1 truncate">{repo.name}</span>

            <button
              onClick={onClearRepo}
              className="rounded-md p-1 text-zinc-300 hover:bg-white/[.08] hover:text-white"
              aria-label="Remove repository"
            >
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
