from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.database import IncidentStatus


class IncidentCreate(BaseModel):
    description: str = Field(min_length=1, max_length=20000)
    telemetry_source: Optional[str] = Field(default=None, max_length=100)


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: Optional[str] = None
    description: str
    service_name: Optional[str] = None
    error_type: Optional[str] = None
    status: IncidentStatus
    created_at: datetime


class SuspectCommitResponse(BaseModel):
    commit_hash: str
    author: str = ""
    message: str
    score: float
    file_path: str
    timestamp: Optional[str] = None
    signals: dict[str, bool] = Field(default_factory=dict)
    diff_snippet: str = ""


class ReportResponse(BaseModel):
    incident: IncidentResponse
    suspect_commits: list[SuspectCommitResponse] = Field(default_factory=list)
    generated_test_code: Optional[str] = None
    generated_test_result: Optional[str] = None
    generated_test_output: Optional[str] = None
    patch_diff: Optional[str] = None
    patch_file: Optional[str] = None
    verification_passed: Optional[bool] = None
    verification_mode: Optional[str] = None
    verification_log: Optional[str] = None
    regression_passed: Optional[bool] = None
    pr_url: Optional[str] = None
    pr_status: Optional[str] = None
    pr_error: Optional[str] = None
    root_cause_explanation: Optional[str] = None
    forensic_analysis: Optional[str] = None
    forensic_evidence: list[str] = Field(default_factory=list)
    forensic_signals: dict[str, bool] = Field(default_factory=dict)
    models: dict[str, Any] = Field(default_factory=dict)
