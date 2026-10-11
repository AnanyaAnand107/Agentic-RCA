const API = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...(init?.headers || {}) },
    cache: "no-store"
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" && data && "detail" in data ? String(data.detail) : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data as T;
}

export type Incident = {
id: string;
title?: string | null;
description: string;
service_name?: string | null;
error_type?: string | null;
status: string;
created_at: string;
};

export type PipelineStatus = {
incident_id: string;
status: string;
error: string | null;
error_type: string | null;
};

export type Report = {
  incident: Incident;
  suspect_commits: Array<{
    commit_hash: string;
    author: string;
    message: string;
    score: number;
    file_path: string;
    timestamp?: string | null;
    signals: Record<string, boolean>;
    diff_snippet: string;
  }>;
  generated_test_code?: string | null;
  generated_test_result?: string | null;
  generated_test_output?: string | null;
  patch_diff?: string | null;
  patch_file?: string | null;
  verification_passed?: boolean | null;
  verification_mode?: string | null;
  verification_log?: string | null;
  root_cause_explanation?: string | null;
  forensic_analysis?: string | null;
  forensic_evidence: string[];
  forensic_signals: Record<string, boolean>;
  models: Record<string, string | null>;
};

export type Health = {
  status: string;
  service: string;
  models: {
    gemini_configured: boolean;
    gemini_model: string;
    huggingface_configured: boolean;
    hf_code_model: string;
    hf_analysis_model: string;
  };
  sandbox_enabled: boolean;
};

export const api = {
  health: () => request<Health>("/api/health"),
  createIncident: (description: string, telemetry_source: string) =>
    request<Incident>("/api/incidents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, telemetry_source })
    }),
  upload: async (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ status: string; workspace: string; repo_root: string }>(`/api/incidents/${id}/upload`, {
      method: "POST",
      body: form
    });
  },
  run: (id: string) => request<{ status: string; incident_id: string }>(`/api/incidents/${id}/run`, { method: "POST" }),
  status: (id: string) => request<PipelineStatus>(`/api/incidents/${id}/status`),
  report: (id: string) => request<Report>(`/api/incidents/${id}/report`),
  approve: (id: string) => request<{ status: string; incident_id: string }>(`/api/incidents/${id}/approve`, { method: "POST" }),
  demo: () => request<{ incident_id: string; message: string }>("/api/demo", { method: "POST" })
};

export function websocketUrl(id: string) {
  const base = (process.env.NEXT_PUBLIC_WS_URL || "ws://127.0.0.1:8000").replace(/\/$/, "");
  return `${base}/ws/incidents/${id}/progress`;
}
