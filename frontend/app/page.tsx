"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  CircleHelp,
  Play,
  RefreshCw,
  RotateCcw,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";

import { AgentPipeline } from "@/components/AgentPipeline";
import { ExecutionLog } from "@/components/ExecutionLog";
import { IncidentComposer } from "@/components/IncidentComposer";
import { ModelStatus } from "@/components/ModelStatus";
import { ResultDashboard } from "@/components/ResultDashboard";
import { OperationalMetricsPanel } from "@/components/OperationalMetrics";
import { api, type Health, type OperationalMetrics, type Report, websocketUrl } from "@/lib/api";
import type { LogEvent } from "@/lib/types";

const POLL_INTERVAL = 3000;
const MAX_WAIT = 5 * 60 * 1000;

export default function Home() {
  const [description, setDescription] = useState("");
  const [source, setSource] = useState("sentry");
  const [repo, setRepo] = useState<File | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [metrics, setMetrics] = useState<OperationalMetrics | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [activeStage, setActiveStage] = useState("");
  const [running, setRunning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<"compose" | "run" | "result">("compose");
  const [elapsed, setElapsed] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const monitorTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef<number>(0);
  const finishedRef = useRef(false);

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
    } catch {
      setHealth(null);
    }
    try { setMetrics(await api.metrics()); } catch { setMetrics(null); }
  }, []);

  useEffect(() => {
    void loadHealth();

    return () => {
      wsRef.current?.close();

      if (monitorTimerRef.current) {
        clearInterval(monitorTimerRef.current);
      }
    };
  }, [loadHealth]);

  useEffect(() => {
    if (!running) {
      return;
    }

    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 1000);

    return () => clearInterval(timer);
  }, [running]);

  const stopMonitoring = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;

    if (monitorTimerRef.current) {
      clearInterval(monitorTimerRef.current);
      monitorTimerRef.current = null;
    }
  }, []);

  const finishWithReport = useCallback(
    async (id: string) => {
      if (finishedRef.current) return;

      finishedRef.current = true;
      stopMonitoring();

      try {
        const finalReport = await api.report(id);

        setReport(finalReport);
        setView("result");
        setRunning(false);

        await loadHealth();
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Could not load the RCA report.",
        );
        setRunning(false);
      }
    },
    [loadHealth, stopMonitoring],
  );

  const monitor = useCallback(
    (id: string): Promise<void> => {
      finishedRef.current = false;

      const poll = async () => {
        if (finishedRef.current) return;

        try {
          const current = await api.status(id);
          const status = current.status;
          const stageProgress: Record<string, number> = { submitted: 0, parsing: 20, forensics: 40, reproducing: 60, patching: 80, verifying: 95, patch_ready: 100, resolved: 100 };
          setProgressPct(stageProgress[status] ?? 0);

          if (status === "parsing") {
            setActiveStage("telemetry_parser");
          }

          if (status === "forensics") {
            setActiveStage("git_forensic");
          }

          if (status === "reproducing") {
            setActiveStage("reproduction");
          }

          if (status === "patching") {
            setActiveStage("patch_synthesizer");
          }

          if (status === "verifying") {
            setActiveStage("verification");
          }

          if (status === "resolved" || status === "patch_ready") {
            await finishWithReport(id);
            return;
          }

          if (status === "failed") {
            finishedRef.current = true;
            stopMonitoring();
            setRunning(false);

            const reason =
              current.error || "The backend did not provide a failure reason.";

            setError(`${current.error_type || "Pipeline error"}: ${reason}`);

            setLogs((existing) => [
              ...existing,
              {
                event: "error",
                current_stage: "pipeline",
                progress_pct: 0,
                log: reason,
              },
            ]);

            return;
          }

          if (Date.now() - startedAtRef.current > MAX_WAIT) {
            finishedRef.current = true;
            stopMonitoring();
            setRunning(false);
            setError(
              "The pipeline exceeded the 5-minute client wait limit. Check the backend terminal for the provider or repository error.",
            );
          }
        } catch {
          // WebSocket remains the live event channel.
          // Polling will retry automatically.
        }
      };

      monitorTimerRef.current = setInterval(() => void poll(), POLL_INTERVAL);

      void poll();

      return new Promise((resolve) => {
        let settled = false;
        const readyTimeout = setTimeout(() => settle(), 5000);
        const settle = () => {
          if (settled) return;
          settled = true;
          clearTimeout(readyTimeout);
          resolve();
        };

        try {
          const ws = new WebSocket(websocketUrl(id));

          wsRef.current = ws;

          ws.onopen = () => {
            ws.send("ready");
            settle();
          };

          ws.onmessage = (event) => {
            try {
              const data = JSON.parse(event.data) as LogEvent;

              setLogs((current) => [...current, data]);

              if (data.current_stage) {
                setActiveStage(data.current_stage);
              }
              if (typeof data.progress_pct === "number") setProgressPct(Math.max(0, Math.min(100, data.progress_pct)));

              if (data.event === "complete") {
                void finishWithReport(id);
              }

              if (data.event === "error") {
                finishedRef.current = true;
                stopMonitoring();
                setRunning(false);

                const message = data.log || "The RCA pipeline failed.";

                setError(message);

                setLogs((current) => [
                  ...current,
                  {
                    ...data,
                    event: "error",
                    log: message,
                  },
                ]);
              }
            } catch {
              setError("Received an invalid progress event from the API.");
            }
          };

          ws.onerror = () => {
            setLogs((current) => [
              ...current,
              {
                event: "warning",
                current_stage: "system",
                progress_pct: 0,
                log: "Live progress connection failed; status polling remains active.",
              },
            ]);
            settle();
          };

          ws.onclose = () => settle();
        } catch {
          settle();
        }
      });
    },
    [finishWithReport, stopMonitoring],
  );

  const reset = () => {
    stopMonitoring();

    setDescription("");
    setSource("sentry");
    setRepo(null);
    setLogs([]);
    setReport(null);
    setActiveStage("");
    setProgressPct(0);
    setRunning(false);
    setError("");
    setView("compose");
  };

  const run = async () => {
    setError("");

    if (!description.trim()) {
      setError("Enter incident telemetry first.");
      return;
    }

    if (!repo) {
      setError("Upload the repository ZIP before running RCA.");
      return;
    }

    startedAtRef.current = Date.now();
    setElapsed(0);
    setRunning(true);
    setReport(null);
    setLogs([]);
    setActiveStage("telemetry_parser");
    setProgressPct(0);
    setView("run");

    try {
      const incident = await api.createIncident(description, source);

      await api.upload(incident.id, repo);

      setLogs([
        {
          event: "pipeline_started",
          current_stage: "telemetry_parser",
          progress_pct: 0,
          log: "Investigation started. Waiting for stage evidence...",
        },
      ]);

      await monitor(incident.id);
      await api.run(incident.id);
    } catch (e) {
      stopMonitoring();
      setError(e instanceof Error ? e.message : "RCA request failed.");
      setRunning(false);
    }
  };

  const runDemo = async () => {
    setError("");
    startedAtRef.current = Date.now();
    setElapsed(0);
    setRunning(true);
    setReport(null);
    setLogs([]);
    setActiveStage("telemetry_parser");
    setProgressPct(0);
    setView("run");

    try {
      const demo = await api.demo();

      setLogs([
        {
          event: "pipeline_started",
          current_stage: "telemetry_parser",
          progress_pct: 0,
          log: "Demo investigation started. Waiting for telemetry analysis...",
        },
      ]);

      await monitor(demo.incident_id);
      await api.run(demo.incident_id);
    } catch (e) {
      stopMonitoring();
      setError(e instanceof Error ? e.message : "Demo request failed.");
      setRunning(false);
    }
  };

  const approve = async () => {
    if (!report) return;

    setApproving(true);

    try {
      const response = await api.approve(report.incident.id);

      setReport((current) =>
        current
          ? {
              ...current,
              incident: {
                ...current.incident,
                status: response.status,
              },
            }
          : current,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record approval.");
    } finally {
      setApproving(false);
    }
  };

  const backendOnline = health?.status === "ok";

  const elapsedLabel =
    elapsed < 60
      ? `${elapsed}s`
      : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;

  return (
    <main className="min-h-screen bg-[#08090c]">
      <header className="sticky top-0 z-30 border-b border-white/[.08] bg-[#08090c]/95 backdrop-blur-xl">
        <div className="mx-auto flex h-[72px] max-w-[1320px] items-center justify-between px-5 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="brand-mark">
              <Zap size={18} fill="currentColor" />
            </div>

            <div>
              <div className="text-base font-bold tracking-tight text-white">
                Agentic RCA
              </div>

              <div className="text-xs font-semibold uppercase tracking-[.16em] text-zinc-300">
                Root-cause intelligence
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div
              className={`hidden items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-semibold sm:flex ${
                backendOnline
                  ? "border-emerald-500/20 bg-emerald-500/[.05] text-emerald-300"
                  : "border-red-500/20 bg-red-500/[.05] text-red-300"
              }`}
            >
              {backendOnline ? <Wifi size={13} /> : <WifiOff size={13} />}

              {backendOnline ? "API online" : "API offline"}
            </div>

            <button
              onClick={loadHealth}
              className="icon-button"
              title="Refresh API status"
            >
              <RefreshCw size={16} />
            </button>

            <button
              onClick={reset}
              className="icon-button"
              title="New incident"
            >
              <RotateCcw size={16} />
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1320px] px-5 py-10 lg:px-8 lg:py-14">
        <section className="mb-10">
          <div className="eyebrow text-indigo-300">
            Evidence-grounded incident response
          </div>

          <h1 className="mt-4 max-w-4xl text-5xl font-semibold leading-[1.02] tracking-[-.045em] text-white sm:text-6xl lg:text-7xl">
            Trace the failure.
            <br />
            <span className="text-indigo-300">Reproduce the cause.</span>
          </h1>

          <p className="mt-6 max-w-3xl text-base leading-8 text-zinc-400">
            Correlate incident telemetry, repository history, reproduction
            results, candidate fixes, and validation evidence in one
            investigation.
          </p>
        </section>

        {error && (
          <div className="mb-6 flex items-start gap-3 rounded-xl border border-red-500/20 bg-red-500/[.05] p-4 text-sm leading-6 text-red-200">
            <Activity size={17} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {view === "compose" && (
          <>
            <IncidentComposer
              description={description}
              source={source}
              repo={repo}
              onDescription={setDescription}
              onSource={setSource}
              onRepo={setRepo}
              onClearRepo={() => setRepo(null)}
            />

            <div className="mt-5 flex flex-col gap-5 rounded-2xl border border-white/[.08] bg-[#0f1014] p-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-base font-semibold text-zinc-200">
                  Five-stage investigation
                </div>

                <div className="mt-1 text-sm text-zinc-300">
                  Parse, correlate, reproduce, synthesize, and validate.
                </div>
              </div>

              <div className="flex gap-3">
                <button
                  onClick={runDemo}
                  disabled={running}
                  className="button-secondary"
                >
                  <Play size={15} />
                  Run demo
                </button>

                <button
                  onClick={run}
                  disabled={running}
                  className="button-primary"
                >
                  <Zap size={15} fill="currentColor" />
                  Run RCA
                </button>
              </div>
            </div>

            <div className="mt-6">
              <ModelStatus health={health} />
            </div>
            <div className="mt-5"><OperationalMetricsPanel metrics={metrics} /></div>
          </>
        )}

        {view !== "compose" && (
          <>
            <AgentPipeline
              activeStage={activeStage}
              complete={view === "result"}
            />

            <div className="mt-5">
              <ModelStatus health={health} />
            </div>

            <div className="mt-5">
              <ExecutionLog logs={logs} />
            </div>
            <div className="mt-5"><OperationalMetricsPanel metrics={metrics} /></div>

            {view === "run" && running && (
              <div className="mt-5 rounded-2xl border border-indigo-500/15 bg-indigo-500/[.035] p-7 text-center">
                <div className="mx-auto mb-4 h-9 w-9 rounded-full border-[3px] border-indigo-400/20 border-t-indigo-400 animate-spin" />

                <div className="text-base font-semibold text-zinc-200">
                  Investigation in progress
                </div>

                <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/[.08]" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Investigation progress">
                  <div className="h-full rounded-full bg-indigo-400 transition-all duration-500" style={{ width: `${progressPct}%` }} />
                </div>
                <div className="mt-1 text-right text-xs text-zinc-400">{progressPct}%</div>
                <div className="mt-2 text-sm text-zinc-300">
                  Current stage:{" "}
                  <span className="font-medium capitalize text-indigo-300">
                    {activeStage.replaceAll("_", " ") || "starting"}
                  </span>
                </div>

                <div className="mt-1 text-sm text-zinc-300">
                  Elapsed time: {elapsedLabel}. Live events are backed by status
                  polling.
                </div>
              </div>
            )}

            {view === "run" && !running && error && (
              <div className="mt-5 rounded-2xl border border-white/[.08] bg-white/[.02] p-5 text-center text-sm text-zinc-300">
                Investigation stopped after {elapsedLabel}.
              </div>
            )}

            {view === "result" && report && (
              <div className="mt-6">
                <ResultDashboard
                  report={report}
                  onApprove={approve}
                  approving={approving}
                />
              </div>
            )}

            {view === "result" && (
              <div className="mt-6 flex justify-end">
                <button onClick={reset} className="button-secondary">
                  <RotateCcw size={15} />
                  Start another investigation
                </button>
              </div>
            )}
          </>
        )}

        <footer className="mt-14 flex flex-col gap-3 border-t border-white/[.1] pt-6 text-sm text-zinc-300 sm:flex-row sm:items-center sm:justify-between">
          <span>Agentic RCA · Evidence first</span>

          <span className="flex items-center gap-2">
            <CircleHelp size={14} />
            Candidate patches require isolated runtime verification before
            deployment.
          </span>
        </footer>
      </div>
    </main>
  );
}
