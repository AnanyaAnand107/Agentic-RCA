"""Stage 4: synthesize a candidate fix with a coding model and review pass.

The agent never writes the patch into the uploaded repository. It returns a
candidate source file and unified diff for human review. Runtime fix
verification is intentionally deferred until sandboxing is added later.
"""

from __future__ import annotations

import ast
import difflib
import logging
import os

from backend.agents.state import AgentState
from backend.llm import extract_json, generate, generate_validated

logger = logging.getLogger(__name__)

BLOCKED_CALLS = {"eval", "exec", "compile", "__import__"}
BLOCKED_MODULES = {"subprocess", "socket", "ctypes"}


def _resolve(repo_path: str, affected_file: str) -> tuple[str, str]:
    if not affected_file:
        return "", ""
    direct = os.path.join(repo_path, affected_file)
    if os.path.isfile(direct):
        return direct, affected_file.replace("\\", "/")
    base = os.path.basename(affected_file)
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache", "node_modules"}]
        for name in files:
            if name == base:
                full = os.path.join(root, name)
                return full, os.path.relpath(full, repo_path).replace("\\", "/")
    return "", ""


def _validate_python(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            return False, f"blocked call: {node.func.id}"
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in BLOCKED_MODULES for alias in node.names):
                return False, "blocked module import"
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in BLOCKED_MODULES:
            return False, "blocked module import"
    return True, "syntax and safety checks passed"


def _parse_patch(text: str) -> tuple[str, str]:
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("patch model did not return an object")
    code = str(data.get("patched_code") or "").strip()
    explanation = str(data.get("root_cause_explanation") or "").strip()
    if not code:
        raise ValueError("patch model returned empty code")
    return code, explanation


def patch_synthesizer(state: AgentState) -> AgentState:
    repo_path = state.get("repo_path", "")
    affected_file = state.get("affected_file", "")
    error_type = state.get("error_type", "Unknown")
    error_message = state.get("error_message", "Unknown")
    generated_test = state.get("generated_test", "")
    forensic = state.get("forensic_analysis", "")
    source_path, relative_path = _resolve(repo_path, affected_file)

    if not source_path:
        return {
            "error": "Patch synthesis skipped because the affected source file could not be resolved.",
            "current_stage": "patch_synthesizer",
            "stage_logs": ["patch synthesis failed: source file unavailable"],
        }

    original = open(source_path, "r", encoding="utf-8", errors="replace").read()
    ext = os.path.splitext(relative_path)[1].lower()
    language = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".java": "Java", ".go": "Go", ".rs": "Rust"}.get(ext, "source code")

    prompt = f"""
TASK: Propose a minimal production-safe fix for the observed defect.

INCIDENT:
error type: {error_type}
error message: {error_message}
affected file: {relative_path}

FORENSIC ANALYSIS:
{forensic or "No additional forensic narrative is available."}

GENERATED REPRODUCTION TEST:
{generated_test or "No reproduction test was generated."}

ORIGINAL {language} SOURCE:
```text
{original[:32000]}
```

RULES:
- Ground the fix only in the incident, forensic evidence, test, and source supplied above.
- Make the smallest change that prevents the observed failure while preserving valid existing behavior.
- Do NOT force the original exception to remain; the objective is to eliminate the observed failure when the input is valid for the product's intended behavior.
- Do NOT invent APIs, files, configuration, database schemas, or dependencies.
- Preserve public interfaces unless the evidence requires otherwise.
- Return the COMPLETE corrected source file.
- Return ONLY JSON with:
{{
  "root_cause_explanation": "one concise sentence",
  "patched_code": "complete corrected file"
}}
""".strip()

    try:
        first_text, provider = generate_validated(
            "patch",
            prompt,
            validator=_parse_patch,
            temperature=0.05,
            max_tokens=7000,
        )
        patched, explanation = _parse_patch(first_text)
    except Exception as exc:
        return {
            "error": f"Patch model unavailable or invalid: {exc}",
            "current_stage": "patch_synthesizer",
            "stage_logs": [f"patch synthesis failed: {exc}"],
        }

    if patched == original:
        return {
            "error": "Patch model returned code identical to the original source.",
            "current_stage": "patch_synthesizer",
            "stage_logs": ["patch rejected: no code change proposed"],
        }

    if ext == ".py":
        valid, reason = _validate_python(patched)
        if not valid:
            # One targeted repair pass.
            repair_prompt = f"""
The following candidate Python patch is invalid.

Reason: {reason}

Original:
```python
{original[:24000]}
```

Candidate:
```python
{patched[:24000]}
```

Incident: {error_type}: {error_message}

Return ONLY JSON with root_cause_explanation and patched_code containing a complete corrected Python file.
""".strip()
            try:
                repair_text, repair_provider = generate("patch", repair_prompt, temperature=0.0, max_tokens=7000)
                repaired, repair_explanation = _parse_patch(repair_text)
                valid, reason = _validate_python(repaired)
                if valid:
                    patched = repaired
                    explanation = repair_explanation or explanation
                    provider = f"{provider} + repair:{repair_provider}"
            except Exception:
                pass
            if not valid:
                return {
                    "error": f"Generated patch failed validation: {reason}",
                    "current_stage": "patch_synthesizer",
                    "stage_logs": [f"patch rejected: {reason}"],
                }

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )

    return {
        "original_code": original,
        "patched_code": patched,
        "diff_text": diff or "(no diff)",
        "patch_file": relative_path,
        "root_cause_explanation": explanation or "Candidate fix generated from supplied evidence.",
        "patch_model": provider,
        "current_stage": "patch_synthesizer",
        "stage_logs": [
            f"candidate generated by {provider}",
            f"candidate file: {relative_path}",
            f"diff lines: {len(diff.splitlines())}",
            "patch not written to repository; awaiting isolated verification",
        ],
    }
