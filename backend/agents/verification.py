"""Stage 5: verify a candidate in an isolated sandbox and decide reflection."""
from __future__ import annotations

import ast
from backend.agents.state import AgentState
from backend.services.sandbox_service import SandboxVerificationService

MAX_REFLECTIONS = 1


def verification(state: AgentState) -> AgentState:
    patched = state.get("patched_code", "")
    patch_file = state.get("patch_file", "")
    diff_text = state.get("diff_text", "")
    count = max(0, int(state.get("reflection_count", 0) or 0))
    repo_path = state.get("repo_path", "")
    generated_test = state.get("generated_test", "")
    test_file = state.get("test_file_name", "tests/test_generated_reproduction.py")
    logs: list[str] = []

    def stop_failure(category: str, message: str, *, retryable: bool = True, sandbox_result: dict | None = None) -> AgentState:
        retry = retryable and count < MAX_REFLECTIONS
        logs.append(message)
        if sandbox_result:
            logs.append(f"sandbox status: {sandbox_result.get('status', 'unknown')}")
        logs.append("reflection retry requested" if retry else "reflection limit reached or retry not applicable")
        return {
            "verification_passed": False,
            "regression_passed": sandbox_result.get("regression_passed") if sandbox_result else None,
            "patch_test_result": category,
            "patch_test_output": message,
            "verification_mode": "docker_sandbox" if sandbox_result else "static_validation",
            "verification_feedback": message[-5000:],
            "reflection_count": count + 1 if retry else count,
            "needs_reflection": retry,
            "verification_log": "\n".join(logs),
            "current_stage": "verification",
            "stage_logs": logs,
            "pr_url": None,
        }

    if not patched:
        return stop_failure("missing_candidate", "No candidate source code was produced.", retryable=False)
    if patch_file.endswith(".py"):
        try:
            ast.parse(patched)
            logs.append("candidate Python syntax validation passed")
        except SyntaxError as exc:
            return stop_failure("syntax_error", f"Candidate syntax validation failed: {exc}. Return a corrected complete source file.")
    if not diff_text or not repo_path:
        return stop_failure("incomplete_candidate", "Verification requires diff_text and a valid repo_path.", retryable=False)
    if not generated_test.strip():
        return stop_failure("missing_reproduction_test", "No generated reproduction test is available; refusing to mark the patch verified.", retryable=False)

    sandbox = SandboxVerificationService()
    result = sandbox.verify_patch(
        original_repo_path=repo_path,
        patch_str=diff_text,
        test_file=test_file,
        generated_test=generated_test,
    )
    logs.append(f"sandbox status: {result.get('status', 'unknown')}")
    logs.append(result.get("reason", "No sandbox reason returned."))

    if result.get("verified") is not True or result.get("regression_passed") is not True:
        category = result.get("error_category") or result.get("status") or "verification_failed"
        # Environment/configuration failures are not fixed by changing source code.
        retryable = category not in {"sandbox_unavailable", "sandbox_setup_error", "invalid_repository", "invalid_test_path", "missing_reproduction_test"}
        return stop_failure(category, result.get("reason", "Sandbox verification failed."), retryable=retryable, sandbox_result=result)

    logs.append("reproduction failed before patch and passed after patch")
    logs.append("full regression suite passed")
    return {
        "verification_passed": True,
        "regression_passed": True,
        "patch_test_result": "passed",
        "patch_test_output": result.get("reason", "Verified in sandbox."),
        "verification_mode": "docker_sandbox",
        "verification_feedback": "",
        "reflection_count": count,
        "needs_reflection": False,
        "verification_log": "\n".join(logs),
        "current_stage": "verification",
        "stage_logs": logs,
        # Publishing is deliberately not done here. It requires explicit human approval.
        "pr_url": None,
    }
