"""REST API and pipeline execution endpoints."""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
import asyncio
import time
from fastapi import Request, Header

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.pipeline import graph
from backend.api.websocket import manager
from backend.config import settings
from backend.database import (
    AsyncSessionLocal,
    CandidatePatch,
    GeneratedTest,
    Incident,
    IncidentStatus,
    SuspectCommit,
    VerificationResult,
    get_db,
)
from backend.llm import model_status
from backend.observability import audit, metric, metrics_summary, sentry_secret_valid, trace_context
from backend.schemas import (
    IncidentCreate,
    IncidentResponse,
    ReportResponse,
    SuspectCommitResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

STAGE_STATUS = {
    "telemetry_parser": IncidentStatus.parsing,
    "git_forensic": IncidentStatus.forensics,
    "reproduction": IncidentStatus.reproducing,
    "patch_synthesizer": IncidentStatus.patching,
    "verification": IncidentStatus.verifying,
}
STAGE_PCT = {
    "telemetry_parser": 20,
    "git_forensic": 40,
    "reproduction": 60,
    "patch_synthesizer": 80,
    "verification": 95,
}
WORKSPACE_ROOT = Path("./workspaces").resolve()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_FILES = 5000


def _safe_extract(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_EXTRACTED_FILES:
            raise ValueError(
                f"archive contains more than {MAX_EXTRACTED_FILES} entries"
            )
        for member in members:
            target = (destination / member.filename).resolve()
            if (
                not str(target).startswith(str(destination.resolve()) + os.sep)
                and target != destination.resolve()
            ):
                raise ValueError("archive contains an unsafe path")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as dest:
                shutil.copyfileobj(source, dest)


def _resolve_repo_root(workspace: Path) -> Path:
    visible = [
        p
        for p in workspace.iterdir()
        if p.name not in {"uploaded.zip"} and not p.name.startswith(".")
    ]
    if len(visible) == 1 and visible[0].is_dir():
        return visible[0]
    return workspace


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "rca-agent-backend",
        "models": model_status(),
        "sandbox_enabled": bool(shutil.which("docker")),
        "sandbox_image": os.getenv("RCA_SANDBOX_IMAGE", "agentic-rca-sandbox:py311"),
    }


@router.post("/incidents", response_model=IncidentResponse)
async def create_incident(data: IncidentCreate, db: AsyncSession = Depends(get_db)):
    incident = Incident(
        description=data.description,
        service_name=data.telemetry_source,
        status=IncidentStatus.submitted,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    await audit("incident.created", incident.id, {"source": data.telemetry_source or "unknown"})
    return incident


@router.post("/incidents/{id}/upload")
async def upload_repo(
    id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Incident).where(Incident.id == id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Repository must be a .zip archive")

    workspace = WORKSPACE_ROOT / id
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path = workspace / "uploaded.zip"

    size = 0
    try:
        with open(zip_path, "wb") as dest:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Repository archive exceeds 50 MB"
                    )
                dest.write(chunk)
        _safe_extract(zip_path, workspace)
        zip_path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, ValueError) as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise HTTPException(
            status_code=400, detail=f"Invalid repository archive: {exc}"
        ) from exc
    except Exception as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise HTTPException(
            status_code=500, detail=f"Repository upload failed: {exc}"
        ) from exc
    finally:
        await file.close()

    repo_root = _resolve_repo_root(workspace)
    return {
        "status": "uploaded",
        "workspace": str(workspace),
        "repo_root": str(repo_root),
    }


@router.get("/incidents", response_model=list[IncidentResponse])
async def list_incidents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Incident).order_by(Incident.created_at.desc()))
    return result.scalars().all()


@router.get("/incidents/{id}", response_model=IncidentResponse)
async def get_incident(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Incident).where(Incident.id == id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.delete("/incidents/{id}")
async def delete_incident(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Incident).where(Incident.id == id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    workspace = WORKSPACE_ROOT / id
    shutil.rmtree(workspace, ignore_errors=True)
    await audit("incident.deleted", id)
    await db.delete(incident)
    await db.commit()
    return {"status": "deleted"}


async def run_pipeline_task(incident_id: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Incident).where(Incident.id == incident_id))
        incident = result.scalar_one_or_none()

        if incident is None:
            return

        try:
            workspace = WORKSPACE_ROOT / incident_id

            if not workspace.exists():
                raise FileNotFoundError(
                    "No repository has been uploaded for this incident"
                )

            repo_path = _resolve_repo_root(workspace)

            initial_state = {
                "incident_id": incident_id,
                "description": incident.description,
                "repo_path": str(repo_path),
                "telemetry_source": incident.service_name or "unknown",
                "otel_context": (incident.telemetry_context or {}).get("otel_context", {}),
                "stage_logs": [],
                "messages": [],
            }

            incident.status = IncidentStatus.parsing
            await db.commit()

            await manager.broadcast(
                incident_id,
                {
                    "event": "pipeline_started",
                    "current_stage": "telemetry_parser",
                    "progress_pct": 0,
                    "log": f"Starting investigation on {repo_path.name}",
                },
            )

            current_stage = {
                "name": "telemetry_parser",
                "pct": 0,
            }
            stage_started = time.perf_counter()
            last_stage = None

            final_state: dict = {}

            async for chunk in graph.astream(initial_state):
                for node_name, state_update in chunk.items():
                    now = time.perf_counter()
                    if last_stage is not None:
                        stage_model = (
                            final_state.get("reproduction_model")
                            if last_stage == "reproduction"
                            else final_state.get("patch_model")
                            if last_stage == "patch_synthesizer"
                            else None
                        )
                        stage_usage = final_state.get("model_usage", {})
                        await metric(
                            incident_id,
                            last_stage,
                            (now - stage_started) * 1000,
                            "success",
                            model=stage_model,
                            usage=stage_usage if isinstance(stage_usage, dict) else {},
                        )
                    last_stage = node_name
                    stage_started = now
                    current_stage["name"] = node_name
                    current_stage["pct"] = STAGE_PCT.get(node_name, 0)

                    final_state.update(state_update)

                    if node_name in STAGE_STATUS:
                        incident.status = STAGE_STATUS[node_name]
                        await db.commit()

                    for line in state_update.get("stage_logs", []):
                        await manager.broadcast(
                            incident_id,
                            {
                                "event": "log",
                                "current_stage": node_name,
                                "progress_pct": STAGE_PCT.get(node_name, 0),
                                "log": line,
                            },
                        )

                    if state_update.get("error"):
                        await metric(
                            incident_id,
                            node_name,
                            (time.perf_counter() - stage_started) * 1000,
                            "failure",
                        )
                        last_stage = None
                        await manager.broadcast(
                            incident_id,
                            {
                                "event": "error",
                                "current_stage": node_name,
                                "progress_pct": STAGE_PCT.get(node_name, 0),
                                "log": state_update["error"],
                            },
                        )
                        break

            if final_state.get("error"):
                raise RuntimeError(final_state["error"])

            incident.error_type = final_state.get("error_type")
            incident.error_message = final_state.get("error_message")
            incident.service_name = final_state.get(
                "affected_service",
                incident.service_name,
            )
            incident.stack_trace = final_state.get("stack_trace")

            incident.telemetry_context = {
                "otel_context": final_state.get(
                    "otel_context",
                    {},
                ),
                "affected_file": final_state.get("affected_file"),
                "affected_line": final_state.get("affected_line"),
                "affected_function": final_state.get("affected_function"),
                "forensic_analysis": final_state.get(
                    "forensic_analysis",
                    "",
                ),
                "forensic_evidence": final_state.get(
                    "forensic_evidence",
                    [],
                ),
                "forensic_signals": final_state.get(
                    "forensic_signals",
                    {},
                ),
                "models": {
                    "reproduction": final_state.get("reproduction_model"),
                    "patch": final_state.get("patch_model"),
                },
                "verification_mode": final_state.get("verification_mode", "not_verified"),
                "verification_passed": final_state.get("verification_passed") is True,
                "regression_passed": final_state.get("regression_passed"),
                "verification_log": final_state.get("verification_log", ""),
                "patch_test_result": final_state.get("patch_test_result", "not_run"),
                "patch_test_output": final_state.get("patch_test_output", ""),
                "pr_url": None,
            }

            incident.title = final_state.get("root_cause_explanation")

            top = final_state.get("top_suspect") or {}

            if top:
                db.add(
                    SuspectCommit(
                        incident_id=incident_id,
                        commit_hash=top.get(
                            "commit_hash",
                            "",
                        ),
                        author=top.get(
                            "author",
                            "",
                        ),
                        message=top.get(
                            "message",
                            "",
                        ),
                        score=top.get(
                            "score",
                            0.0,
                        ),
                        file_path=top.get(
                            "file_path",
                            "",
                        ),
                        diff_snippet=top.get(
                            "diff_snippet",
                            "",
                        ),
                        timestamp=top.get(
                            "timestamp",
                            "",
                        ),
                        signals=top.get(
                            "signals",
                            {},
                        ),
                    )
                )

            if final_state.get("generated_test"):
                db.add(
                    GeneratedTest(
                        incident_id=incident_id,
                        test_code=final_state["generated_test"],
                        test_file_name=final_state.get(
                            "test_file_name",
                            "generated::test_reproduction.py",
                        ),
                        execution_result=final_state.get("patch_test_result") or final_state.get("test_result", "unknown"),
                        output_log=final_state.get("patch_test_output") or final_state.get("test_output", ""),
                    )
                )

            patch_id = None

            if final_state.get("diff_text"):
                patch = CandidatePatch(
                    incident_id=incident_id,
                    file_path=final_state.get(
                        "patch_file",
                        "",
                    ),
                    original_code=final_state.get(
                        "original_code",
                        "",
                    ),
                    patched_code=final_state.get(
                        "patched_code",
                        "",
                    ),
                    diff_text=final_state.get(
                        "diff_text",
                        "",
                    ),
                    is_verified=(final_state.get("verification_passed") is True),
                )

                db.add(patch)
                await db.flush()
                patch_id = patch.id

            if patch_id:
                db.add(
                    VerificationResult(
                        incident_id=incident_id,
                        patch_id=patch_id,
                        test_passed=(final_state.get("verification_passed") is True),
                        regression_passed=final_state.get("regression_passed"),
                        output_log=final_state.get(
                            "verification_log",
                            "",
                        ),
                    )
                )

            if last_stage is not None:
                await metric(incident_id, last_stage, (time.perf_counter() - stage_started) * 1000, "success",
                             model=(final_state.get("reproduction_model") if last_stage == "reproduction" else final_state.get("patch_model") if last_stage == "patch_synthesizer" else None),
                             usage=final_state.get("model_usage", {}) if isinstance(final_state.get("model_usage", {}), dict) else {},
                             db=db)

            # A verified candidate is ready for human review; it is not resolved until merged/deployed.
            incident.status = IncidentStatus.patch_ready

            await db.commit()
            await audit("pipeline.completed", incident_id, {"status": incident.status.value, "has_patch": bool(final_state.get("diff_text"))})

            await manager.broadcast(
                incident_id,
                {
                    "event": "complete",
                    "current_stage": "done",
                    "progress_pct": 100,
                    "log": (
                        "Analysis complete. Candidate verification status: "
                        + ("verified; awaiting human approval" if final_state.get("verification_passed") is True else "not verified; inspect the report and logs")
                    ),
                },
            )

        except Exception as exc:
            logger.exception(
                "Pipeline failed for incident %s",
                incident_id,
            )

            incident.status = IncidentStatus.failed
            incident.error_type = type(exc).__name__
            incident.error_message = str(exc)

            await db.commit()
            await audit("pipeline.failed", incident_id, {"error_type": type(exc).__name__, "error": str(exc)[:1000]})
            if 'last_stage' in locals() and last_stage is not None:
                await metric(incident_id, last_stage, (time.perf_counter() - stage_started) * 1000, "failure")

            await manager.broadcast(
                incident_id,
                {
                    "event": "error",
                    "current_stage": "error",
                    "progress_pct": 0,
                    "log": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
            )


@router.post("/incidents/{id}/run")
async def run_pipeline(
    id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Incident).where(Incident.id == id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status in {
        IncidentStatus.parsing,
        IncidentStatus.forensics,
        IncidentStatus.reproducing,
        IncidentStatus.patching,
        IncidentStatus.verifying,
    }:
        raise HTTPException(status_code=409, detail="Pipeline is already running")
    if not (WORKSPACE_ROOT / id).exists():
        raise HTTPException(
            status_code=400, detail="Upload a repository before running the pipeline"
        )
    background_tasks.add_task(run_pipeline_task, id)
    await audit("pipeline.started", id)
    return {"status": "started", "incident_id": id}


@router.get("/incidents/{id}/status")
async def get_pipeline_status(
    id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Incident).where(Incident.id == id))

    incident = result.scalar_one_or_none()

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    return {
        "incident_id": id,
        "status": incident.status.value,
        "error": (
            incident.error_message if incident.status == IncidentStatus.failed else None
        ),
        "error_type": (
            incident.error_type if incident.status == IncidentStatus.failed else None
        ),
    }


@router.get("/incidents/{id}/report", response_model=ReportResponse)
async def get_report(id: str, db: AsyncSession = Depends(get_db)):
    inc_res = await db.execute(select(Incident).where(Incident.id == id))
    incident = inc_res.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    suspects_res = await db.execute(
        select(SuspectCommit).where(SuspectCommit.incident_id == id)
    )
    suspects = suspects_res.scalars().all()
    test_res = await db.execute(
        select(GeneratedTest)
        .where(GeneratedTest.incident_id == id)
        .order_by(GeneratedTest.id.desc())
    )
    test = test_res.scalars().first()
    patch_res = await db.execute(
        select(CandidatePatch)
        .where(CandidatePatch.incident_id == id)
        .order_by(CandidatePatch.id.desc())
    )
    patch = patch_res.scalars().first()
    verify_res = await db.execute(
        select(VerificationResult)
        .where(VerificationResult.incident_id == id)
        .order_by(VerificationResult.id.desc())
    )
    verification = verify_res.scalars().first()

    context = incident.telemetry_context or {}
    return {
        "incident": IncidentResponse.model_validate(incident),
        "suspect_commits": [
            SuspectCommitResponse(
                commit_hash=s.commit_hash,
                author=s.author or "",
                message=s.message or "",
                score=s.score or 0.0,
                file_path=s.file_path or "",
                timestamp=s.timestamp,
                signals=s.signals or {},
                diff_snippet=s.diff_snippet or "",
            )
            for s in suspects
        ],
        "generated_test_code": test.test_code if test else None,
        "generated_test_result": test.execution_result if test else None,
        "generated_test_output": test.output_log if test else None,
        "patch_diff": patch.diff_text if patch else None,
        "patch_file": patch.file_path if patch else None,
        "verification_passed": patch.is_verified if patch else None,
        "verification_mode": context.get("verification_mode", "not_verified") if verification else None,
        "regression_passed": context.get("regression_passed"),
        "pr_url": context.get("pr_url"),
        "pr_status": context.get("pr_status"),
        "pr_error": context.get("pr_error"),
        "verification_log": verification.output_log if verification else None,
        "root_cause_explanation": incident.title,
        "forensic_analysis": context.get("forensic_analysis"),
        "forensic_evidence": context.get("forensic_evidence", []),
        "forensic_signals": context.get("forensic_signals", {}),
        "models": context.get("models", {}),
    }


@router.get("/incidents/{id}/patch")
async def get_patch(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CandidatePatch)
        .where(CandidatePatch.incident_id == id)
        .order_by(CandidatePatch.id.desc())
    )
    patch = result.scalars().first()
    if patch is None:
        raise HTTPException(status_code=404, detail="No patch found for this incident")
    return {
        "file_path": patch.file_path,
        "diff_text": patch.diff_text,
        "original_code": patch.original_code,
        "patched_code": patch.patched_code,
        "is_verified": patch.is_verified,
        "verification_mode": "docker_sandbox" if patch.is_verified else "not_verified",
    }


@router.post("/incidents/{id}/approve")
async def approve_patch(id: str, db: AsyncSession = Depends(get_db)):
    """Human-triggered PR publishing; refuses unverified candidates."""
    inc_result = await db.execute(select(Incident).where(Incident.id == id))
    incident = inc_result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = await db.execute(
        select(CandidatePatch)
        .where(CandidatePatch.incident_id == id)
        .order_by(CandidatePatch.id.desc())
    )
    patch = result.scalars().first()
    if patch is None:
        raise HTTPException(status_code=404, detail="No patch found for this incident")
    if patch.is_verified is not True:
        raise HTTPException(status_code=409, detail="Cannot publish an unverified patch. Sandbox reproduction and regression checks must pass first.")

    # This endpoint is called only after the human presses the approval action.
    from backend.services.github_service import GitHubService
    context = dict(incident.telemetry_context or {})
    publisher = GitHubService()
    pr_result = publisher.create_pull_request(
        incident_id=id,
        patch_str=patch.diff_text or "",
        forensic_evidence=context.get("forensic_analysis") or incident.title or "Forensic analysis completed.",
        verification_summary=context.get("verification_log") or "Sandbox reproduction and regression checks passed.",
        verification_passed=patch.is_verified is True,
        human_approved=True,
    )
    context["pr_status"] = pr_result.get("status", "unknown")
    context["pr_url"] = pr_result.get("pr_url")
    context["pr_error"] = pr_result.get("reason")
    incident.telemetry_context = context
    await db.commit()
    await audit("patch.publish_requested", id, {"patch_id": patch.id, "pr_status": pr_result.get("status"), "pr_url": pr_result.get("pr_url")})
    if pr_result.get("status") not in {"created", "exists"}:
        raise HTTPException(status_code=502, detail={"message": "Patch was approved, but GitHub PR creation did not complete.", "github": pr_result})
    return {
        "status": pr_result.get("status"),
        "patch_id": patch.id,
        "pr_url": pr_result.get("pr_url"),
        "pr_number": pr_result.get("pr_number"),
        "verification": "verified",
        "human_approval": "recorded",
    }


@router.post("/demo")
async def run_demo(db: AsyncSession = Depends(get_db)):
    sample_path = Path("./sample_data/sample_incident.json")
    sample_repo = Path("./sample_data/sample_repo")
    if not sample_path.exists():
        raise HTTPException(status_code=500, detail="Sample incident data is unavailable")

    data = json.loads(sample_path.read_text(encoding="utf-8"))
    incident = Incident(
        description=data["description"],
        service_name=data.get("service_name"),
        status=IncidentStatus.submitted,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    await audit("demo.created", incident.id, {"source": "bundled_demo"})

    workspace = WORKSPACE_ROOT / incident.id
    workspace.mkdir(parents=True, exist_ok=True)
    if sample_repo.exists():
        shutil.copytree(sample_repo, workspace / sample_repo.name, dirs_exist_ok=True)
    else:
        # Keep the demo deterministic even when the optional sample repository was not packaged.
        demo_repo = workspace / "sample_repo"
        demo_repo.mkdir(parents=True, exist_ok=True)
        (demo_repo / "pricing.py").write_text(
            'def apply_discount(item, discount_pct):\n    return item["price"] * (1 - discount_pct / 100)\n',
            encoding="utf-8",
        )
    return {"incident_id": incident.id, "message": "Demo incident is ready"}


@router.get("/metrics")
async def operational_metrics():
    """Aggregated persisted per-agent latency and failure metrics."""
    return await metrics_summary()


@router.get("/audit")
async def audit_records(limit: int = 100, db: AsyncSession = Depends(get_db)):
    from backend.database import AuditEvent
    result = await db.execute(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(max(1, min(limit, 500))))
    return [{"id": row.id, "incident_id": row.incident_id, "action": row.action,
             "details": row.details, "created_at": row.created_at.isoformat()} for row in result.scalars().all()]


@router.post("/webhooks/sentry")
async def sentry_webhook(request: Request, x_sentry_hook_signature: str | None = Header(default=None),
                         db: AsyncSession = Depends(get_db)):
    """Accept Sentry issue/event webhooks; configure SENTRY_WEBHOOK_SECRET when available."""
    if not sentry_secret_valid(x_sentry_hook_signature):
        raise HTTPException(status_code=401, detail="Invalid Sentry webhook secret")
    payload = await request.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    issue = data.get("issue", {}) if isinstance(data, dict) else {}
    event = data.get("event", {}) if isinstance(data, dict) else {}
    title = issue.get("title") or event.get("title") or payload.get("message") or "Sentry incident"
    trace = trace_context(dict(request.headers))
    event_context = event.get("contexts", {}) if isinstance(event, dict) else {}
    trace.update({"sentry_event_id": event.get("event_id") or payload.get("event_id"),
                  "sentry_issue_id": issue.get("id"), "sentry_project": issue.get("project"),
                  "sentry_url": issue.get("web_url") or issue.get("url"),
                  "sentry_contexts": event_context if isinstance(event_context, dict) else {}})
    exception_data = event.get("exception", {}) if isinstance(event, dict) else {}
    exception_values = exception_data.get("values", []) if isinstance(exception_data, dict) else []
    exception_type = (exception_values[0].get("type") if exception_values and isinstance(exception_values[0], dict) else None)
    incident = Incident(description=str(event.get("message") or title)[:20000],
                        title=str(title)[:500], service_name="sentry",
                        error_type=str(exception_type or "SentryIssue")[:200],
                        error_message=str(event.get("message") or title)[:4000],
                        telemetry_context=trace, status=IncidentStatus.submitted)
    db.add(incident)
    await db.commit()
    await db.refresh(incident)
    await audit("sentry.webhook_received", incident.id, {"issue_id": issue.get("id"), "event_id": event.get("event_id")})
    return {"status": "accepted", "incident_id": incident.id}
