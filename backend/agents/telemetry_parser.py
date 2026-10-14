"""Stage 1: extract structured incident facts with a model and local validation."""

from __future__ import annotations

import logging
import os
import re

from backend.agents.state import AgentState
from backend.llm import extract_json, generate_validated

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



def _validate_payload(text: str) -> dict:
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("telemetry response must be a JSON object")
    for key in ("stack_trace", "otel_context"):
        if key in data and data[key] is not None and not isinstance(data[key], (list, dict)):
            raise ValueError(f"{key} has an invalid type")
    if data.get("affected_line") is not None:
        line = int(data["affected_line"])
        if line < 1:
            raise ValueError("affected_line must be positive")
    confidence = data.get("confidence")
    if confidence is not None and not 0 <= float(confidence) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return data


def _fallback_parse(description: str, files: list[str]) -> dict:
    """Extract common stack-frame formats without depending on a model."""
    frames: list[dict] = []
    patterns = (
        re.compile(r'File [\"\'](?P<file>[^\"\']+)[\"\'], line (?P<line>\d+)(?:, in (?P<function>[^\n]+))?'),
        re.compile(r'\bat (?P<function>[\w.$<>/-]+)?\s*\((?P<file>[^():]+\.(?:js|jsx|ts|tsx|py|java|go|rs)):(?P<line>\d+)(?::\d+)?\)'),
        re.compile(r'\bat (?P<function>[\w.$<>]+)\((?P<file>[^():]+\.java):(?P<line>\d+)\)'),
    )
    for pattern in patterns:
        for match in pattern.finditer(description):
            raw_file = (match.groupdict().get("file") or "").replace("\\", "/")
            normalized = raw_file
            while normalized.startswith("./"):
                normalized = normalized[2:]
            repo_file = _nearest_file(normalized, files)
            try:
                line = int(match.group("line"))
            except (ValueError, TypeError):
                line = None
            function = (match.groupdict().get("function") or "").strip() or None
            frames.append({"file": repo_file or raw_file, "line": line, "function": function})
    # Keep the first occurrence of each concrete frame while preserving stack order.
    unique = []
    seen = set()
    for frame in frames:
        key = (frame["file"], frame["line"], frame["function"])
        if key not in seen:
            seen.add(key)
            unique.append(frame)
    exception = re.search(r"(?m)^([A-Za-z_][\w.]*(?:Error|Exception|Failure|Interrupt|Exit))(?::\s*(.*))?$", description)
    if not exception:
        exception = re.search(r"\b([A-Za-z_][\w.]*(?:Error|Exception|Failure))(?::\s*([^\n]+))?", description)
    error_type = exception.group(1) if exception else "Unknown"
    error_message = (exception.group(2) or "").strip() if exception else ""
    primary = next((f for f in unique if _nearest_file(f["file"], files)), unique[0] if unique else {})
    return {
        "error_type": error_type,
        "error_message": error_message or description[:500].strip(),
        "affected_file": _nearest_file(primary.get("file", ""), files),
        "affected_line": primary.get("line"),
        "affected_function": primary.get("function") or "",
        "stack_trace": unique,
        "affected_service": None,
        "otel_context": {},
        "confidence": 0.35 if unique else 0.15,
    }


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
        text, provider = generate_validated(
            "telemetry", prompt, validator=_validate_payload, temperature=0.0, max_tokens=1800
        )
        data = _validate_payload(text)
        mode = f"model:{provider}"
    except Exception as exc:
        logger.warning("Model telemetry extraction unavailable; using deterministic parser: %s", exc)
        data = _fallback_parse(description, files)
        provider = "deterministic-fallback"
        mode = f"fallback (model error: {type(exc).__name__})"

    affected_file = _nearest_file(str(data.get("affected_file") or ""), files)
    stack = data.get("stack_trace") or []
    normalized_stack = []
    if isinstance(stack, list):
        for frame in stack[:100]:
            if not isinstance(frame, dict):
                continue
            frame_file = str(frame.get("file") or frame.get("filename") or "")
            normalized_stack.append({
                "file": _nearest_file(frame_file, files) or frame_file.replace("\\", "/"),
                "line": int(frame["line"]) if str(frame.get("line", "")).isdigit() and int(frame["line"]) > 0 else None,
                "function": str(frame.get("function") or frame.get("method") or "") or None,
            })
    if not affected_file:
        primary = next((frame for frame in normalized_stack if _nearest_file(frame["file"], files)), None)
        if primary:
            affected_file = _nearest_file(primary["file"], files)
            data["affected_line"] = data.get("affected_line") or primary.get("line")
            data["affected_function"] = data.get("affected_function") or primary.get("function") or ""

    line = data.get("affected_line")
    try:
        line = int(line) if line is not None and int(line) > 0 else None
    except (TypeError, ValueError):
        line = None

    logs = [
        f"extraction: {mode}",
        f"parsed error: {data.get('error_type') or 'unknown'}: {data.get('error_message') or 'unknown'}",
        f"affected location: {affected_file or 'unknown'}:{line or '?'}::{data.get('affected_function') or '?'}",
        f"stack frames: {len(normalized_stack)}; repository indexed: {len(files)} readable files",
    ]
    return {
        "error_type": str(data.get("error_type") or "Unknown"),
        "error_message": str(data.get("error_message") or "Unknown"),
        "affected_service": str(data.get("affected_service") or state.get("telemetry_source") or "unknown"),
        "affected_file": affected_file,
        "affected_line": line,
        "affected_function": str(data.get("affected_function") or ""),
        "stack_trace": normalized_stack,
        "otel_context": data.get("otel_context") if isinstance(data.get("otel_context"), dict) else {},
        "current_stage": "telemetry_parser",
        "stage_logs": logs,
    }
