"""Stage 1: extract structured incident facts with a model and local validation."""

from __future__ import annotations

import logging
import os

from backend.agents.state import AgentState
from backend.llm import extract_json, generate

logger = logging.getLogger(__name__)

SKIP_DIRS = {"__pycache__", "node_modules", "venv", "env", "dist", "build", ".pytest_cache", ".mypy_cache", ".git"}


def _list_repo_files(repo_path: str, max_files: int = 150) -> list[str]:
    paths: list[str] = []
    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, repo_path).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.read(256)
            except OSError:
                continue
            paths.append(rel)
            if len(paths) >= max_files:
                return paths
    return sorted(paths)


def _nearest_file(name: str, files: list[str]) -> str:
    if name in files:
        return name
    base = os.path.basename(name)
    matches = [path for path in files if os.path.basename(path) == base]
    return matches[0] if len(matches) == 1 else ""


def telemetry_parser(state: AgentState) -> AgentState:
    description = state.get("description", "")[:12000]
    repo_path = state.get("repo_path", "")
    files = _list_repo_files(repo_path) if os.path.isdir(repo_path) else []

    prompt = f"""
TASK: Parse a production incident into structured facts.

RULES:
- Use ONLY facts explicitly present in the incident or inferable from a supplied stack trace.
- Never invent a file, line, function, service, timestamp, or exception.
- If a field is unknown, use null or an empty list.
- affected_file MUST be one of the repository paths when repository paths are supplied.
- Extract the most specific stack-frame location available.
- Return ONLY JSON matching this schema:
{{
  "error_type": string|null,
  "error_message": string|null,
  "affected_service": string|null,
  "affected_file": string|null,
  "affected_line": integer|null,
  "affected_function": string|null,
  "stack_trace": [{{"file": string, "line": integer|null, "function": string|null}}],
  "otel_context": object,
  "confidence": number
}}

INCIDENT:
{description}

REPOSITORY PATHS:
{chr(10).join(files[:150]) if files else "(repository path list unavailable)"}
""".strip()

    try:
        text, provider = generate("telemetry", prompt, temperature=0.0, max_tokens=1800)
        data = extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("model returned a non-object JSON value")

        affected_file = _nearest_file(str(data.get("affected_file") or ""), files)
        stack = data.get("stack_trace") or []
        if not isinstance(stack, list):
            stack = []

        line = data.get("affected_line")
        if line is not None:
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = None

        logs = [
            f"model: {provider}",
            f"parsed error: {data.get('error_type') or 'unknown'}: {data.get('error_message') or 'unknown'}",
            f"affected location: {affected_file or 'unknown'}:{line or '?'}::{data.get('affected_function') or '?'}",
            f"repository indexed: {len(files)} readable files",
        ]
        return {
            "error_type": str(data.get("error_type") or "Unknown"),
            "error_message": str(data.get("error_message") or "Unknown"),
            "affected_service": str(data.get("affected_service") or state.get("telemetry_source") or "unknown"),
            "affected_file": affected_file,
            "affected_line": line,
            "affected_function": str(data.get("affected_function") or ""),
            "stack_trace": stack,
            "otel_context": data.get("otel_context") if isinstance(data.get("otel_context"), dict) else {},
            "current_stage": "telemetry_parser",
            "stage_logs": logs,
        }
    except Exception as exc:
        logger.exception("Telemetry parser failed")
        return {
            "error": f"Telemetry parsing failed: {exc}",
            "current_stage": "telemetry_parser",
            "stage_logs": [f"telemetry parser error: {exc}"],
        }
