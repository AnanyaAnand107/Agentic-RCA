import asyncio
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, Float, ForeignKey, JSON, String, Text, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from backend.config import settings

Base = declarative_base()


class IncidentStatus(str, enum.Enum):
    submitted = "submitted"
    parsing = "parsing"
    forensics = "forensics"
    reproducing = "reproducing"
    patching = "patching"
    verifying = "verifying"
    patch_ready = "patch_ready"
    resolved = "resolved"
    failed = "failed"


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    service_name = Column(String, nullable=True)
    error_type = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    stack_trace = Column(JSON, nullable=True)
    telemetry_context = Column(JSON, nullable=True)
    status = Column(SAEnum(IncidentStatus), default=IncidentStatus.submitted, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    suspect_commits = relationship("SuspectCommit", back_populates="incident", cascade="all, delete-orphan")
    candidate_patches = relationship("CandidatePatch", back_populates="incident", cascade="all, delete-orphan")
    generated_tests = relationship("GeneratedTest", cascade="all, delete-orphan")
    verification_results = relationship("VerificationResult", cascade="all, delete-orphan")


class SuspectCommit(Base):
    __tablename__ = "suspect_commits"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    commit_hash = Column(String, nullable=False)
    author = Column(String)
    message = Column(Text)
    score = Column(Float)
    file_path = Column(String)
    diff_snippet = Column(Text)
    timestamp = Column(String)
    signals = Column(JSON, nullable=True)
    incident = relationship("Incident", back_populates="suspect_commits")


class GeneratedTest(Base):
    __tablename__ = "generated_tests"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    test_code = Column(Text)
    test_file_name = Column(String)
    execution_result = Column(String)
    output_log = Column(Text)


class CandidatePatch(Base):
    __tablename__ = "candidate_patches"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(String)
    original_code = Column(Text)
    patched_code = Column(Text)
    diff_text = Column(Text)
    is_verified = Column(Boolean, default=False)
    incident = relationship("Incident", back_populates="candidate_patches")


class VerificationResult(Base):
    __tablename__ = "verification_results"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    patch_id = Column(String, ForeignKey("candidate_patches.id", ondelete="CASCADE"), nullable=True)
    test_passed = Column(Boolean, nullable=True)
    regression_passed = Column(Boolean, nullable=True)
    output_log = Column(Text)
    verified_at = Column(DateTime, default=datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AgentMetric(Base):
    __tablename__ = "agent_metrics"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id = Column(String, nullable=False, index=True)
    agent = Column(String, nullable=False, index=True)
    duration_ms = Column(Float, nullable=False, default=0)
    outcome = Column(String, nullable=False, default="success")
    model = Column(String, nullable=True)
    usage = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 30} if _is_sqlite else {},
)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
_db_initialized = False
_db_initialization_lock = asyncio.Lock()


if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite_connection(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


async def init_db():
    global _db_initialized
    if _db_initialized:
        return
    async with _db_initialization_lock:
        if _db_initialized:
            return
        async with engine.connect() as conn:
            if _is_sqlite:
                await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.run_sync(Base.metadata.create_all)
            await conn.commit()
        _db_initialized = True


async def ensure_db_ready():
    await init_db()


async def get_db():
    await ensure_db_ready()
    async with AsyncSessionLocal() as session:
        yield session
