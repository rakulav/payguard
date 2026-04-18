"""Database models and async engine setup with pgvector support."""

from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    Text,
    JSON,
    create_engine,
    text,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pgvector.sqlalchemy import Vector

from app.config import get_settings

settings = get_settings()

async_engine = create_async_engine(
    settings.database_url, echo=False, pool_size=20, max_overflow=10
)
AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

sync_engine = create_engine(settings.database_url_sync, echo=False, pool_size=5)
SyncSession = sessionmaker(sync_engine)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id = Column(String, primary_key=True)
    step = Column(Integer)
    type = Column(String, index=True)
    amount = Column(Float)
    name_orig = Column(String, index=True)
    old_balance_org = Column(Float)
    new_balance_orig = Column(Float)
    name_dest = Column(String, index=True)
    old_balance_dest = Column(Float)
    new_balance_dest = Column(Float)
    is_fraud = Column(Boolean, index=True, default=False)
    is_flagged_fraud = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String)
    device_fingerprint = Column(String)
    merchant_category = Column(String)
    country_code = Column(String)
    embedding = Column(Vector(384))


class Investigation(Base):
    __tablename__ = "investigations"

    id = Column(String, primary_key=True)
    transaction_id = Column(String, index=True)
    status = Column(
        String, default="pending"
    )  # pending, in_progress, completed, approved, rejected
    verdict = Column(String)  # fraud, legitimate, inconclusive
    confidence = Column(Float)
    recommendation = Column(String)  # freeze, escalate, monitor, clear
    evidence = Column(JSON)
    triage_result = Column(JSON)
    behavior_result = Column(JSON)
    synthesis_result = Column(JSON)
    agent_trace = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_by = Column(String)
    approval_decision = Column(String)
    cost_usd = Column(Float, default=0.0)
    model_breakdown = Column(JSONB)
    token_usage = Column(JSONB)


async def update_investigation_cost_fields(
    investigation_id: str,
    *,
    cost_usd: float,
    model_breakdown: list | None,
    token_usage: dict | None,
) -> None:
    """Persist LLM usage totals on the investigation row when it already exists (e.g. before approval wait ends)."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = res.scalar_one_or_none()
        if inv:
            inv.cost_usd = cost_usd
            if model_breakdown is not None:
                inv.model_breakdown = model_breakdown
            if token_usage is not None:
                inv.token_usage = token_usage
            inv.updated_at = datetime.utcnow()
            await session.commit()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True)
    investigation_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(128), nullable=False)
    action = Column(String(128), nullable=False)
    prior_state = Column(JSONB)
    new_state = Column(JSONB)
    reason = Column(Text)


async def _ensure_investigation_columns(conn):
    """Idempotent column adds for existing deployments."""
    for stmt in (
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS model_breakdown JSONB",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS token_usage JSONB",
    ):
        await conn.execute(text(stmt))


async def init_db():
    """Create tables and extensions."""
    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_investigation_columns(conn)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
