"""Small, dependency-light audit and operational metrics helpers."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AgentMetric, AuditEvent, AsyncSessionLocal, ensure_db_ready


def trace_context(headers: dict[str, str] | None = None) -> dict[str, Any]:
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    traceparent = headers.get("traceparent", "")
    context: dict[str, Any] = {}
    if len(traceparent.split("-")) == 4:
        version, trace_id, span_id, flags = traceparent.split("-")
        if len(trace_id) == 32 and len(span_id) == 16:
            context["trace_id"] = trace_id
            context["span_id"] = span_id
            context["trace_flags"] = flags
            context["traceparent"] = traceparent
    if headers.get("sentry-trace"):
        context["sentry_trace"] = headers["sentry-trace"]
    if headers.get("baggage"):
        context["baggage"] = headers["baggage"][:4000]
    return context


async def audit(action: str, incident_id: str | None = None, details: dict | None = None):
    await ensure_db_ready()
    async with AsyncSessionLocal() as db:
        db.add(AuditEvent(action=action, incident_id=incident_id, details=details or {}))
        await db.commit()


async def metric(incident_id: str, agent: str, duration_ms: float, outcome: str,
                 model: str | None = None, usage: dict | None = None,
                 db: AsyncSession | None = None):
    await ensure_db_ready()
    record = AgentMetric(incident_id=incident_id, agent=agent,
                         duration_ms=max(0, duration_ms), outcome=outcome,
                         model=model, usage=usage or {})
    if db is not None:
        db.add(record)
        return
    async with AsyncSessionLocal() as db:
        db.add(record)
        await db.commit()


async def metrics_summary() -> dict:
    await ensure_db_ready()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AgentMetric))).scalars().all()
        by_agent: dict[str, dict] = {}
        for row in rows:
            item = by_agent.setdefault(row.agent, {"runs": 0, "failures": 0, "latency_ms_total": 0.0, "models": {}})
            item["runs"] += 1
            item["failures"] += int(row.outcome != "success")
            item["latency_ms_total"] += row.duration_ms or 0
            if row.model:
                item["models"][row.model] = item["models"].get(row.model, 0) + 1
        for item in by_agent.values():
            item["avg_latency_ms"] = round(item.pop("latency_ms_total") / item["runs"], 2)
            item["failure_rate"] = round(item["failures"] / item["runs"], 4)
        return {"agent_metrics": by_agent, "total_agent_runs": len(rows)}


def sentry_secret_valid(secret: str | None) -> bool:
    expected = os.getenv("SENTRY_WEBHOOK_SECRET", "").strip()
    return not expected or (secret is not None and secret == expected)
