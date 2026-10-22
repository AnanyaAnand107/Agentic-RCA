import { AlertTriangle, CheckCircle2, GitCommit, ShieldCheck } from "lucide-react";
import type { Report } from "@/lib/api";
import { CodePanel } from "./CodePanel";

export function ResultDashboard({ report, onApprove, approving }: { report: Report; onApprove: () => void; approving: boolean }) {
  const suspect = report.suspect_commits[0];
  const verification = report.verification_passed === true ? "verified" : report.verification_passed === false ? "not-verified" : "pending";

  return <section className="space-y-4">
    <div className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
      <div className="panel p-6">
        <div className="eyebrow">Root cause candidate</div>
        <h2 className="mt-2 text-2xl font-semibold leading-snug tracking-tight text-white">{suspect?.message || report.root_cause_explanation || "No commit-level candidate returned"}</h2>
        {suspect && <div className="mt-4 flex flex-wrap gap-2"><Badge icon={<GitCommit size={14}/>} text={suspect.commit_hash} /><Badge text={suspect.file_path} /><Badge text={`${Math.round(suspect.score * 100)}% evidence score`} accent /></div>}
        <p className="mt-5 text-base leading-7 text-zinc-300">{report.forensic_analysis || report.root_cause_explanation || "The forensic agent did not provide a narrative."}</p>
      </div>
      <div className="panel p-6">
        <div className="eyebrow">Verification state</div>
        <div className={`mt-3 flex items-center gap-3 text-base font-semibold ${verification === "verified" ? "text-emerald-300" : verification === "not-verified" ? "text-red-300" : "text-amber-200"}`}>
          {verification === "verified" ? <CheckCircle2 size={18}/> : verification === "not-verified" ? <AlertTriangle size={18}/> : <ShieldCheck size={18}/>} {verification === "verified" ? "Runtime verified" : verification === "not-verified" ? "Not verified — review logs" : "Awaiting verification"}
        </div>
        <p className="mt-3 whitespace-pre-line text-sm leading-6 text-zinc-300">{report.verification_log || "No sandbox verification result is available yet."}</p>
        {report.patch_diff && <button onClick={onApprove} disabled={approving || report.verification_passed !== true || Boolean(report.pr_url)} className="mt-5 w-full rounded-xl border border-white/[.12] bg-white/[.04] px-4 py-3 text-sm font-semibold text-zinc-100 transition hover:bg-white/[.08] disabled:opacity-40">{approving ? "Creating pull request..." : report.pr_url ? "Pull request created" : report.verification_passed === true ? "Approve & open GitHub PR" : "PR publishing requires successful sandbox verification"}</button>}
        {report.pr_url && <a href={report.pr_url} target="_blank" rel="noreferrer" className="mt-3 block break-all text-sm text-indigo-300 underline">Open GitHub pull request</a>}
        {report.pr_status && !report.pr_url && <p className="mt-3 text-sm text-amber-200">GitHub status: {report.pr_status}</p>}
      </div>
    </div>

    <div className="grid gap-4 lg:grid-cols-2"><CodePanel title="Generated regression test" language="pytest" code={report.generated_test_code}/><CodePanel title={`Candidate patch${report.patch_file ? ` · ${report.patch_file}` : ""}`} language="diff" code={report.patch_diff}/></div>

    <div className="grid gap-4 lg:grid-cols-[1fr_.8fr]">
      <CodePanel title="Reproduction output" language="pytest" code={report.generated_test_output || report.generated_test_result}/>
      <div className="panel p-5">
        <div className="eyebrow">Evidence signals</div>
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.entries(report.forensic_signals).length ? Object.entries(report.forensic_signals).map(([key, value]) => (
            <span key={key} className={`rounded-lg border px-3 py-2 text-xs font-medium ${value ? "border-emerald-500/20 bg-emerald-500/[.05] text-emerald-200" : "border-white/[.1] text-zinc-300"}`}>
              {value ? "✓ " : "○ "}{key.replaceAll("_", " ")}
            </span>
          )) : <span className="text-sm text-zinc-300">No signal summary returned.</span>}
        </div>
        <div className="mt-6">
          <div className="eyebrow">Model routing</div>
          <div className="mt-3 space-y-3 text-sm text-zinc-300">
            {Object.entries(report.models).map(([key, value]) => (
              <div key={key} className="flex flex-wrap justify-between gap-2">
                <span>{key.replaceAll("_", " ")}</span>
                <span className="max-w-full break-all text-right font-mono text-zinc-200 sm:max-w-[65%]">{value || "unknown"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>

    {report.forensic_evidence.length > 0 && <div className="panel p-5"><div className="eyebrow">Forensic evidence</div><ul className="mt-4 space-y-3">{report.forensic_evidence.map((item, i) => <li key={i} className="flex gap-3 text-sm leading-6 text-zinc-300"><span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-indigo-300"/>{item}</li>)}</ul></div>}
  </section>;
}

function Badge({ text, icon, accent }: { text: string; icon?: React.ReactNode; accent?: boolean }) { return <span className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium ${accent ? "border-indigo-400/25 bg-indigo-500/[.08] text-indigo-200" : "border-white/[.12] bg-white/[.03] text-zinc-200"}`}>{icon}{text}</span>; }
