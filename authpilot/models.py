"""Persistence: authorization cases, the agent's step log, findings and the audit trail."""

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from authpilot.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CaseStatus:
    NEW = "new"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"  # agent finished; coordinator must approve or reject
    NEEDS_INFO = "needs_info"  # agent asked the provider for missing documentation
    SUBMITTED = "submitted"
    APPROVED = "approved"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    ERROR = "error"


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    patient_name: Mapped[str] = mapped_column(String(200))
    service_code: Mapped[str] = mapped_column(String(64))
    service_name: Mapped[str] = mapped_column(String(200))
    policy_id: Mapped[str] = mapped_column(String(64))
    ordering_provider: Mapped[str] = mapped_column(String(200))
    requested_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default=CaseStatus.NEW, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="initial")  # initial | appeal

    # Agent output
    recommendation: Mapped[str | None] = mapped_column(String(30))  # ready|needs_info|not_supported
    summary: Mapped[str | None] = mapped_column(Text)  # medical-necessity summary / appeal letter
    clarification: Mapped[str | None] = mapped_column(Text)  # question sent to the provider
    planner: Mapped[str | None] = mapped_column(String(100))
    prep_seconds: Mapped[float | None] = mapped_column(Float)

    # Payer interaction
    payer_reference: Mapped[str | None] = mapped_column(String(64))
    payer_decision_reason: Mapped[str | None] = mapped_column(Text)
    prior_denial_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    findings: Mapped[list["Finding"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="Finding.criterion_id"
    )
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="AgentStep.id"
    )


class Finding(Base):
    """The agent's verdict on one policy criterion, with cited chart evidence."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    criterion_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))  # met | not_met | unclear
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)  # [{resource_id, quote}]

    case: Mapped[Case] = relationship(back_populates="findings")


class AgentStep(Base):
    """One entry in the agent's visible timeline (reasoning, tool call, tool result...)."""

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # reasoning|tool_call|tool_result|error|done
    tool: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="steps")


class AuditEvent(Base):
    """Append-only record of who did what to which case - agent, user or payer."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(
        String(100)
    )  # agent:<planner> | user:<name> | payer | system
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChartAddition(Base):
    """FHIR resources added to a chart after load (e.g. the provider answering a question).

    Kept in the database rather than on disk so every app instance sees them - required on
    serverless platforms where the filesystem is read-only and instances are short-lived.
    """

    __tablename__ = "chart_additions"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    resource: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PayerSubmission(Base):
    """State of the simulated payer (Northwind Health Plan)."""

    __tablename__ = "payer_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), unique=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    policy_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))  # initial | appeal
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|denied
    decision_reason: Mapped[str | None] = mapped_column(Text)
    status_checks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
