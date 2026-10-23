import asyncio

from backend import observability


def test_metric_uses_pipeline_session_without_committing(monkeypatch):
    class Session:
        def __init__(self):
            self.added = []
            self.commits = 0

        def add(self, record):
            self.added.append(record)

        async def commit(self):
            self.commits += 1

    async def database_ready():
        return None

    monkeypatch.setattr(observability, "ensure_db_ready", database_ready)
    session = Session()

    asyncio.run(
        observability.metric(
            "incident-1",
            "verification",
            12.5,
            "success",
            db=session,
        )
    )

    assert len(session.added) == 1
    assert session.added[0].incident_id == "incident-1"
    assert session.added[0].agent == "verification"
    assert session.commits == 0
