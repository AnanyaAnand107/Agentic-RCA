"""Stage 5: candidate validation and reflection-loop decision.

The sandbox runner is a separate integration. When it supplies patch_test_result,
this node consumes its result and returns actionable feedback to the synthesizer.
Without sandbox results, this node deliberately does not claim a patch is verified.
"""

import ast
from backend.agents.state import AgentState

MAX_REFLECTIONS = 1


def verification(state: AgentState) -> AgentState:
    patched = state.get("patched_code", "")
    patch_file = state.get("patch_file", "")
    test_result = state.get("patch_test_result", "").lower()
    test_output = state.get("patch_test_output", "")
    count = int(state.get("reflection_count", 0) or 0)
    logs: list[str] = []

    if not patched:
        return {
            "verification_passed": None,
            "regression_passed": None,
            "verification_mode": "pre_isolation",
            "verification_feedback": "No candidate patch was produced.",
            "needs_reflection": False,
            "current_stage": "verification",
            "stage_logs": ["verification stopped: no candidate patch"],
        }

    if patch_file.endswith(".py"):
        try:
            ast.parse(patched)
            logs.append("candidate Python source parses successfully")
        except SyntaxError as exc:
            feedback = f"Candidate syntax validation failed: {exc}. Return a corrected complete file."
            retry = count < MAX_REFLECTIONS
            logs.append(feedback)
            return {
                "verification_passed": False,
                "regression_passed": None,
                "verification_mode": "static_validation",
                "verification_feedback": feedback,
                "reflection_count": count + 1 if retry else count,
                "needs_reflection": retry,
                "verification_log": "\n".join(logs),
                "current_stage": "verification",
                "stage_logs": logs,
            }

    if test_result in {"failed", "error", "timeout", "not_reproduced"}:
        feedback = (
            "The isolated patch test did not pass. Revise the patch using this execution output; "
            "do not weaken or delete the regression assertion.\n" + test_output[-5000:]
        )
        retry = count < MAX_REFLECTIONS
        logs.append(f"isolated patch test: {test_result}")
        logs.append("reflection retry requested" if retry else "reflection limit reached")
        return {
            "verification_passed": False,
            "regression_passed": state.get("regression_passed"),
            "verification_mode": "sandbox" if test_result != "not_reproduced" else "sandbox_reproduction",
            "verification_feedback": feedback,
            "reflection_count": count + 1 if retry else count,
            "needs_reflection": retry,
            "verification_log": "\n".join(logs),
            "current_stage": "verification",
            "stage_logs": logs,
        }

    if test_result == "passed":
        regression_result = state.get("regression_passed")
        passed = regression_result is True
        logs.append("isolated generated patch test passed")
        logs.append(f"regression suite passed: {regression_result is True}")
        if not passed:
            feedback = "The regression suite did not pass. Use its failure output to correct the patch without weakening tests."
            retry = count < MAX_REFLECTIONS
            return {
                "verification_passed": False,
                "regression_passed": False,
                "verification_mode": "sandbox",
                "verification_feedback": feedback + "\n" + test_output[-5000:],
                "reflection_count": count + 1 if retry else count,
                "needs_reflection": retry,
                "verification_log": "\n".join(logs),
                "current_stage": "verification",
                "stage_logs": logs,
            }
        logs.append("patch and regression checks passed in the supplied sandbox result")
        return {
            "verification_passed": True,
            "regression_passed": True,
            "verification_mode": "sandbox",
            "verification_feedback": "",
            "needs_reflection": False,
            "verification_log": "\n".join(logs),
            "current_stage": "verification",
            "stage_logs": logs,
        }

    logs.append("runtime patch verification is pending sandbox integration")
    return {
        "verification_passed": None,
        "regression_passed": state.get("regression_passed"),
        "verification_mode": "pre_isolation",
        "verification_feedback": "",
        "needs_reflection": False,
        "verification_log": "\n".join(logs),
        "current_stage": "verification",
        "stage_logs": logs,
    }
