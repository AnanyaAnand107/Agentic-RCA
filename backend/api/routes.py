"""REST API and pipeline execution endpoints."""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
import asyncio

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
        "sandbox_enabled": False,
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

            heartbeat_running = True

            async def heartbeat():
                while heartbeat_running:
                    await asyncio.sleep(10)

                    if not heartbeat_running:
                        break

                    await manager.broadcast(
                        incident_id,
                        {
                            "event": "heartbeat",
                            "current_stage": current_stage["name"],
                            "progress_pct": current_stage["pct"],
                            "log": (
                                f"Still working on "
                                f"{current_stage['name'].replace('_', ' ')}. "
                                "Waiting for the current operation to return."
                            ),
                        },
                    )

            heartbeat_task = asyncio.create_task(heartbeat())

            final_state: dict = {}

            try:
                async for chunk in graph.astream(initial_state):
                    for node_name, state_update in chunk.items():
                        current_stage["name"] = node_name
                        current_stage["pct"] = STAGE_PCT.get(
                            node_name,
                            0,
                        )

                        final_state.update(state_update)

                        if node_name in STAGE_STATUS:
                            incident.status = STAGE_STATUS[node_name]
                            await db.commit()

                        for line in state_update.get(
                            "stage_logs",
                            [],
                        ):
                            await manager.broadcast(
                                incident_id,
                                {
                                    "event": "log",
                                    "current_stage": node_name,
                                    "progress_pct": STAGE_PCT.get(
                                        node_name,
                                        0,
                                    ),
                                    "log": line,
                                },
                            )

                        if state_update.get("error"):
                            await manager.broadcast(
                                incident_id,
                                {
                                    "event": "error",
                                    "current_stage": node_name,
                                    "progress_pct": STAGE_PCT.get(
                                        node_name,
                                        0,
                                    ),
                                    "log": state_update["error"],
                                },
                            )
                            break

            finally:
                heartbeat_running = False
                heartbeat_task.cancel()

                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

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
                        execution_result=final_state.get(
                            "test_result",
                            "unknown",
                        ),
                        output_log=final_state.get(
                            "test_output",
                            "",
                        ),
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
                        test_passed=(final_state.get("test_result") == "reproduced"),
                        regression_passed=final_state.get("regression_passed"),
                        output_log=final_state.get(
                            "verification_log",
                            "",
                        ),
                    )
                )

            incident.status = (
                IncidentStatus.resolved
                if final_state.get("verification_passed") is True
                else IncidentStatus.patch_ready
            )

            await db.commit()

            await manager.broadcast(
                incident_id,
                {
                    "event": "complete",
                    "current_stage": "done",
                    "progress_pct": 100,
                    "log": (
                        "Analysis complete; runtime patch "
                        "verification is pending isolated execution"
                        if incident.status == IncidentStatus.patch_ready
                        else "Pipeline completed and patch verified"
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
        "verification_mode": "pre_isolation" if verification else None,
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
        "verification_mode": "pre_isolation",
    }


@router.post("/incidents/{id}/approve")
async def approve_patch(id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CandidatePatch)
        .where(CandidatePatch.incident_id == id)
        .order_by(CandidatePatch.id.desc())
    )
    patch = result.scalars().first()
    if patch is None:
        raise HTTPException(status_code=404, detail="No patch found for this incident")
    # Approval records human acceptance but does not falsely claim runtime verification.
    return {
        "status": "approved_for_manual_review",
        "patch_id": patch.id,
        "verification": "pending_isolated_execution",
    }


@router.post("/demo")
async def run_demo(db: AsyncSession = Depends(get_db)):
    sample_path = Path("./sample_data/sample_incident.json")
    sample_repo = Path("./sample_data/sample_repo")
    if not sample_path.exists() or not sample_repo.exists():
        raise HTTPException(status_code=500, detail="Sample data is unavailable")

    data = json.loads(sample_path.read_text(encoding="utf-8"))
    incident = Incident(
        description=data["description"],
        service_name=data.get("service_name"),
        status=IncidentStatus.submitted,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    workspace = WORKSPACE_ROOT / incident.id
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sample_repo, workspace / sample_repo.name, dirs_exist_ok=True)
    return {"incident_id": incident.id, "message": "Demo incident is ready"}
