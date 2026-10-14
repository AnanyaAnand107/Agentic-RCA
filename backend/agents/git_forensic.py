"""Stage 2: repository forensics grounded in actual Git evidence."""

from __future__ import annotations

import logging
import os
import re

from backend.agents.state import AgentState
from backend.llm import extract_json, generate_validated

logger = logging.getLogger(__name__)

SKIP_DIRS = {"__pycache__", "node_modules", "venv", "env", "dist", "build", ".pytest_cache", ".mypy_cache", ".git"}


def _tokenize(value: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value or "")}


def _line_distance(diff_text: str, line: int | None) -> bool:
    if not line:
        return False
    for hunk in re.findall(r"@@[^\n]*\+(\d+)(?:,(\d+))?[^\n]*@@", diff_text):
        start = int(hunk[0])
        count = int(hunk[1] or 1)
        if start <= line <= start + max(count - 1, 0):
            return True
    return False


def _repo_files(repo_path: str) -> list[str]:
    result = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), repo_path).replace("\\", "/")
            result.append(rel)
    return sorted(result)


def _commit_diff(commit, parent, target_file: str) -> str:
    """Return a commit's forward diff so added-line hunks use current line numbers."""
    try:
        if parent is not None:
            diffs = parent.diff(commit, create_patch=True)
            for diff in diffs:
                if diff.a_path == target_file or diff.b_path == target_file:
                    raw = diff.diff
                    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        else:
            from git.objects import NULL_TREE
            # Ask Git for the root commit's forward diff (empty tree -> first commit).
            return str(commit.repo.git.diff(NULL_TREE, commit.hexsha, "--", target_file))
    except Exception as exc:
        logger.debug("Could not read diff for %s: %s", getattr(commit, "hexsha", "unknown"), exc)
    return ""


def _rank_commits(repo_path: str, affected_file: str, affected_function: str, error_message: str, line: int | None) -> tuple[list[dict], dict[str, bool]]:
    from git import Repo

    repo = Repo(repo_path)
    commits = list(repo.iter_commits(max_count=80))
    incident_tokens = _tokenize(f"{error_message} {affected_function}")
    candidates = []
    blame_match = False
    blame_hash = ""

    if affected_file and line:
        try:
            blame = repo.blame("HEAD", affected_file)
            cursor = 0
            for commit, lines in blame:
                if cursor < line <= cursor + len(lines):
                    blame_hash = commit.hexsha
                    blame_match = True
                    break
                cursor += len(lines)
        except Exception:
            pass

    for index, commit in enumerate(commits):
        changed = list(commit.stats.files.keys())
        path_overlap = bool(affected_file and affected_file in changed)
        parent = commit.parents[0] if commit.parents else None
        diff = _commit_diff(commit, parent, affected_file) if affected_file else ""
        function_overlap = bool(affected_function and affected_function in diff)
        line_match = _line_distance(diff, line)
        message_overlap = bool(incident_tokens & _tokenize(commit.message))
        is_blame = bool(blame_hash and commit.hexsha == blame_hash)
        temporal = 1.0 / (index + 1)

        # Evidence weights are fixed, but the score is derived only from observed signals.
        score = (
            0.38 * float(path_overlap)
            + 0.20 * float(line_match)
            + 0.17 * float(function_overlap)
            + 0.15 * float(is_blame)
            + 0.06 * float(message_overlap)
            + 0.04 * temporal
        )

        if score <= 0:
            continue

        candidates.append({
            "commit_hash": commit.hexsha[:12],
            "full_hash": commit.hexsha,
            "author": str(commit.author),
            "message": commit.message.strip()[:160],
            "timestamp": commit.committed_datetime.isoformat(),
            "score": round(score, 4),
            "file_path": affected_file if path_overlap else (changed[0] if changed else ""),
            "diff_snippet": diff[:1800],
            "signals": {
                "file_overlap": path_overlap,
                "line_proximity": line_match,
                "function_overlap": function_overlap,
                "blame_match": is_blame,
                "message_similarity": message_overlap,
                "temporal_proximity": index < 5,
            },
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    signals = {
        "file_overlap": bool(candidates and candidates[0]["signals"]["file_overlap"]),
        "line_proximity": bool(candidates and candidates[0]["signals"]["line_proximity"]),
        "function_overlap": bool(candidates and candidates[0]["signals"]["function_overlap"]),
        "blame_match": blame_match,
        "message_similarity": bool(candidates and candidates[0]["signals"]["message_similarity"]),
        "temporal_proximity": bool(candidates and candidates[0]["signals"]["temporal_proximity"]),
    }
    return candidates[:8], signals


def _llm_review(state: AgentState, candidates: list[dict]) -> tuple[str, list[str], str, str]:
    if not candidates:
        return "", [], "", ""

    evidence = []
    for c in candidates[:6]:
        evidence.append(
            f"COMMIT {c['commit_hash']} | {c['timestamp']} | {c['message']} | score={c['score']}\n"
            f"FILE={c['file_path']}\nSIGNALS={c['signals']}\nDIFF:\n{c['diff_snippet'][:1400]}"
        )

    prompt = f"""
TASK: Perform evidence-grounded root-cause analysis of a production incident.

INCIDENT FACTS:
error={state.get('error_type')}: {state.get('error_message')}
file={state.get('affected_file')}
line={state.get('affected_line')}
function={state.get('affected_function')}

CANDIDATE COMMITS (all values are extracted from the real repository):
{chr(10).join(evidence)}

RULES:
- Do not invent commits, files, authors, timestamps, or code changes.
- A candidate score is only a ranking signal, never proof.
- Explicitly separate observed evidence from inference.
- Prefer a commit only when multiple independent signals support it.
- If evidence is insufficient, say so.
- Return ONLY JSON:
{{
  "analysis": "2-4 concise sentences",
  "evidence": ["specific observed fact", "specific observed fact"],
  "selected_commit_hash": "one supplied 12-character hash or null",
  "confidence": "high|medium|low|insufficient"
}}
""".strip()

    valid_hashes = {c["commit_hash"] for c in candidates}

    def validate_response(raw: str) -> dict:
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("forensic response must be a JSON object")
        if not isinstance(data.get("analysis", ""), str):
            raise ValueError("analysis must be a string")
        if not isinstance(data.get("evidence", []), list):
            raise ValueError("evidence must be a list")
        selected = data.get("selected_commit_hash")
        if selected not in (None, "") and selected not in valid_hashes:
            raise ValueError("selected commit must match one of the supplied candidates")
        if str(data.get("confidence", "insufficient")).lower() not in {"high", "medium", "low", "insufficient"}:
            raise ValueError("confidence must be high, medium, low, or insufficient")
        return data

    try:
        text, provider = generate_validated(
            "forensics", prompt, validator=validate_response, temperature=0.0, max_tokens=1800
        )
        data = validate_response(text)
        selected = str(data.get("selected_commit_hash") or "")
        if selected not in valid_hashes:
            selected = candidates[0]["commit_hash"] if candidates else ""
        analysis = str(data.get("analysis") or "Evidence is insufficient for a confident attribution.")
        evidence_out = [str(x) for x in (data.get("evidence") or []) if isinstance(x, (str, int, float))][:6]
        return analysis, evidence_out, provider, selected
    except Exception as exc:
        logger.warning("Forensic model failed: %s", exc)
        return "Evidence was ranked deterministically; model review was unavailable.", [], "", candidates[0]["commit_hash"] if candidates else ""


def git_forensic(state: AgentState) -> AgentState:
    repo_path = state.get("repo_path", "")
    affected_file = state.get("affected_file", "")
    affected_function = state.get("affected_function", "")
    error_message = state.get("error_message", "")
    line = state.get("affected_line")

    if not os.path.isdir(repo_path):
        return {"error": "Repository workspace does not exist.", "current_stage": "git_forensic", "stage_logs": ["repository workspace missing"]}

    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        # A source-only upload can still be analysed, but it cannot produce a commit-level RCA.
        files = _repo_files(repo_path)
        return {
            "suspect_commits": [],
            "top_suspect": {},
            "forensic_analysis": "No .git history was supplied, so commit attribution is unavailable.",
            "forensic_evidence": [f"repository contains {len(files)} readable paths"],
            "forensic_signals": {},
            "current_stage": "git_forensic",
            "stage_logs": ["no .git directory; commit-level forensics skipped"],
        }

    try:
        candidates, signals = _rank_commits(repo_path, affected_file, affected_function, error_message, line)
        analysis, evidence, provider, selected_hash = _llm_review(state, candidates)

        selected = next((c for c in candidates if c["commit_hash"] == selected_hash), candidates[0] if candidates else {})
        logs = [
            f"git history scanned: {len(candidates)} evidence-bearing candidates",
            f"top candidate: {selected.get('commit_hash') or 'none'}",
            f"model review: {provider or 'unavailable'}",
            f"selected commit: {selected.get('commit_hash') or 'none'} (validated against ranked candidates)",
        ]
        return {
            "suspect_commits": candidates,
            "top_suspect": selected,
            "forensic_analysis": analysis,
            "forensic_evidence": evidence,
            "forensic_signals": signals,
            "current_stage": "git_forensic",
            "stage_logs": logs,
        }
    except Exception as exc:
        logger.exception("Git forensic failed")
        return {"error": f"Git forensics failed: {exc}", "current_stage": "git_forensic", "stage_logs": [f"git forensic error: {exc}"]}
