"""Stage 5: pre-isolation validation.

Sandbox execution is intentionally NOT implemented yet. This stage validates
that the generated candidate is structurally safe and syntactically valid,
and reports that runtime fix verification remains pending.
"""

from __future__ import annotations

import ast
from backend.agents.state import AgentState


def verification(state: AgentState) -> AgentState:
    patched = state.get("patched_code", "")
    patch_file = state.get("patch_file", "")
    generated_test = state.get("generated_test", "")
    test_result = state.get("test_result", "")

    logs = []
    syntax_valid = True

    if not patched:
        return {
            "verification_passed": None,
            "regression_passed": None,
            "verification_mode": "pre_isolation",
            "verification_log": "No candidate patch was produced.",
            "current_stage": "verification",
            "stage_logs": ["verification stopped: no candidate patch"],
        }

    if patch_file.endswith(".py"):
        try:
            ast.parse(patched)
            logs.append("candidate Python source parses successfully")
        except SyntaxError as exc:
            syntax_valid = False
            logs.append(f"candidate Python syntax invalid: {exc}")

    if generated_test and test_result == "reproduced":
        logs.append("baseline reproduction test passed against the original code")
    elif generated_test:
        logs.append(f"baseline reproduction status: {test_result}")

    logs.append("runtime fix verification is pending isolated execution")

    return {
        # None is intentional: without isolation we cannot claim the patch fixes the crash.
        "verification_passed": None if syntax_valid else False,
        "regression_passed": None,
        "verification_mode": "pre_isolation",
        "verification_log": "\n".join(logs),
        "current_stage": "verification",
        "stage_logs": logs,
    }
