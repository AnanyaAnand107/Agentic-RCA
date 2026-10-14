"""LangGraph orchestration for the five RCA agents with a bounded repair loop."""

from langgraph.graph import END, StateGraph

from backend.agents.git_forensic import git_forensic
from backend.agents.patch_synthesizer import patch_synthesizer
from backend.agents.reproduction import reproduction
from backend.agents.state import AgentState
from backend.agents.telemetry_parser import telemetry_parser
from backend.agents.verification import verification

MAX_REFLECTIONS = 1


def should_continue(state: AgentState) -> str:
    return "end" if state.get("error") else "continue"


def after_verification(state: AgentState) -> str:
    """Allow at most one evidence-driven patch revision after a failed check."""
    if state.get("needs_reflection") and state.get("reflection_count", 0) <= MAX_REFLECTIONS:
        return "reflect"
    return "end"


def build_pipeline():
    workflow = StateGraph(AgentState)
    workflow.add_node("telemetry_parser", telemetry_parser)
    workflow.add_node("git_forensic", git_forensic)
    workflow.add_node("reproduction", reproduction)
    workflow.add_node("patch_synthesizer", patch_synthesizer)
    workflow.add_node("verification", verification)
    workflow.set_entry_point("telemetry_parser")

    for current, nxt in [
        ("telemetry_parser", "git_forensic"),
        ("git_forensic", "reproduction"),
        ("reproduction", "patch_synthesizer"),
        ("patch_synthesizer", "verification"),
    ]:
        workflow.add_conditional_edges(
            current, should_continue, {"continue": nxt, "end": END}
        )

    workflow.add_conditional_edges(
        "verification", after_verification, {"reflect": "patch_synthesizer", "end": END}
    )
    return workflow.compile()


graph = build_pipeline()
