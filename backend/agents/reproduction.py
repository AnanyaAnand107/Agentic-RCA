"""Stage 3: generate and execute a focused regression reproducer.

No sandbox is used in this version. Generated tests are written to the system
temporary directory, never into the uploaded repository. Strict AST checks
limit imports and dangerous operations before pytest is invoked.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from backend.agents.state import AgentState
from backend.llm import _strip_fences, generate

logger = logging.getLogger(__name__)

ALLOWED_IMPORTS = {"pytest"}
BLOCKED_NAMES = {
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "system", "popen", "run", "Popen", "call", "check_call", "check_output",
}
BLOCKED_MODULES = {"subprocess", "os", "shutil", "socket", "requests", "httpx", "pathlib", "ctypes"}


def _resolve_source(repo_path: str, affected_file: str) -> tuple[str, str]:
    if not affected_file:
        return "", ""
    direct = os.path.join(repo_path, affected_file)
    if os.path.isfile(direct):
        return direct, affected_file.replace("\\", "/")
    basename = os.path.basename(affected_file)
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache", "node_modules"}]
        for name in files:
            if name == basename:
                full = os.path.join(root, name)
                return full, os.path.relpath(full, repo_path).replace("\\", "/")
    return "", ""


def _module_name(relative_path: str) -> str:
    path = relative_path.replace("\\", "/")
    if not path.endswith(".py"):
        return ""
    return path[:-3].replace("/", ".").lstrip(".")


def _validate_test(code: str, module: str, function: str) -> tuple[bool, str]:
    code = _strip_fences(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"

    imported_modules = []
    found_pytest_raises = False
    found_function = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_NAMES:
                return False, f"blocked call: {node.func.id}"
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in BLOCKED_NAMES:
                    return False, f"blocked call: {node.func.attr}"
                if isinstance(node.func.value, ast.Name) and node.func.value.id in BLOCKED_MODULES:
                    return False, f"blocked module call: {node.func.value.id}.{node.func.attr}"
                if node.func.attr == "raises" and isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest":
                    found_pytest_raises = True
        elif isinstance(node, ast.Name) and node.id == function:
            found_function = True

    allowed = set(ALLOWED_IMPORTS) | {module}
    for imported in imported_modules:
        if imported not in allowed and not imported.startswith(module + "."):
            return False, f"unsupported import: {imported}"

    if not found_pytest_raises:
        return False, "test must use pytest.raises to encode the observed crash"
    if function and not found_function:
        return False, f"generated test does not reference affected function {function}"
    return True, "validated"


def _write_temp_test(code: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix="_test.py", prefix="rca_repro_", delete=False, encoding="utf-8"
    )
    handle.write(code)
    handle.close()
    return handle.name


def reproduction(state: AgentState) -> AgentState:
    repo_path = state.get("repo_path", "")
    affected_file = state.get("affected_file", "")
    affected_function = state.get("affected_function", "")
    error_type = state.get("error_type", "Unknown")
    error_message = state.get("error_message", "Unknown")
    line = state.get("affected_line")

    src_path, relative_path = _resolve_source(repo_path, affected_file)
    if not src_path:
        return {
            "generated_test": "",
            "test_file_name": "",
            "test_result": "skipped",
            "test_output": "affected source file could not be resolved",
            "current_stage": "reproduction",
            "stage_logs": ["reproduction skipped: source file unavailable"],
        }

    if not src_path.endswith(".py"):
        return {
            "generated_test": "",
            "test_file_name": "",
            "test_result": "skipped",
            "test_output": "automatic pytest reproduction currently supports Python source files",
            "current_stage": "reproduction",
            "stage_logs": ["reproduction skipped: affected file is not Python"],
        }

    source = Path(src_path).read_text(encoding="utf-8", errors="replace")
    module = _module_name(relative_path)

    prompt = f"""
TASK: Generate one minimal pytest regression test that reproduces the observed production exception.

INCIDENT:
error type: {error_type}
error message: {error_message}
affected file: {relative_path}
affected line: {line}
affected function: {affected_function}
Python import module: {module}

SOURCE OF TRUTH — COMPLETE AFFECTED FILE:
```python
{source[:24000]}
```

REQUIREMENTS:
1. Import ONLY pytest and the affected module/function.
2. Do not use subprocess, os, pathlib, network calls, filesystem writes, eval, exec, or shell commands.
3. Call the real affected function with inputs derived from the source and incident; never invent an unrelated API.
4. Encode the observed exception using pytest.raises(<exception type>).
5. Keep the test minimal and deterministic.
6. Return ONLY Python code. No markdown and no explanation.
""".strip()

    try:
        test_code, provider = generate("reproduction", prompt, temperature=0.0, max_tokens=2600)
    except Exception as exc:
        return {
            "generated_test": "",
            "test_file_name": "",
            "test_result": "skipped",
            "test_output": f"no code model available: {exc}",
            "current_stage": "reproduction",
            "stage_logs": [f"reproduction skipped: {exc}"],
        }

    test_code = _strip_fences(test_code)
    valid, reason = _validate_test(test_code, module, affected_function)
    if not valid:
        return {
            "generated_test": test_code,
            "test_file_name": "",
            "test_result": "invalid",
            "test_output": reason,
            "reproduction_model": provider,
            "current_stage": "reproduction",
            "stage_logs": [f"generated test rejected: {reason}"],
        }

    temp_test = _write_temp_test(test_code)
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [repo_path, env.get("PYTHONPATH", "")]))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", temp_test, "-q", "--tb=short"],
            cwd=repo_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        output = (result.stdout + result.stderr)[-5000:]
        test_result = "reproduced" if result.returncode == 0 else "not_reproduced"
        logs = [
            f"source resolved: {relative_path}",
            f"test generated with: {provider}",
            f"test validation: {reason}",
            f"pytest result: {test_result}",
        ]
        return {
            "generated_test": test_code,
            "test_file_name": "generated::test_reproduction.py",
            "test_result": test_result,
            "test_output": output,
            "reproduction_model": provider,
            "current_stage": "reproduction",
            "stage_logs": logs,
        }
    except subprocess.TimeoutExpired:
        return {
            "generated_test": test_code,
            "test_file_name": "generated::test_reproduction.py",
            "test_result": "timeout",
            "test_output": "pytest exceeded 45 seconds",
            "reproduction_model": provider,
            "current_stage": "reproduction",
            "stage_logs": ["reproduction timed out after 45 seconds"],
        }
    finally:
        try:
            os.unlink(temp_test)
        except OSError:
            pass
