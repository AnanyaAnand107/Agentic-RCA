from typing import Annotated, Optional, TypedDict
import operator


class AgentState(TypedDict, total=False):
    incident_id: str
    description: str
    repo_path: str
    telemetry_source: str

    error_type: str
    error_message: str
    stack_trace: list[dict]
    affected_service: str
    affected_file: str
    affected_line: int | None
    affected_function: str
    otel_context: dict

    suspect_commits: list[dict]
    top_suspect: dict
    forensic_analysis: str
    forensic_evidence: list[str]
    forensic_signals: dict[str, bool]

    generated_test: str
    test_file_name: str
    test_result: str
    test_output: str
    reproduction_model: str
    patch_test_result: str
    patch_test_output: str
    sandbox_status: str
    sandbox_result: dict
    pr_url: Optional[str]

    original_code: str
    patched_code: str
    diff_text: str
    patch_file: str
    root_cause_explanation: str
    patch_model: str

    verification_passed: Optional[bool]
    regression_passed: Optional[bool]
    verification_log: str
    verification_mode: str
    verification_feedback: str
    reflection_count: int
    needs_reflection: bool

    current_stage: str
    stage_logs: Annotated[list[str], operator.add]
    error: Optional[str]
    messages: Annotated[list, operator.add]
