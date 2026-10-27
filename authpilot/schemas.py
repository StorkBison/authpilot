from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


def _assume_utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on round-trip; everything is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_assume_utc)]


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: str
    patient_name: str
    service_code: str
    service_name: str
    policy_id: str
    ordering_provider: str
    requested_on: date
    status: str
    mode: str
    recommendation: str | None
    summary: str | None
    clarification: str | None
    planner: str | None
    prep_seconds: float | None
    payer_reference: str | None
    payer_decision_reason: str | None
    prior_denial_reason: str | None
    updated_at: UTCDateTime


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    criterion_id: str
    status: str
    rationale: str
    evidence: list[dict]


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    kind: str
    tool: str | None
    content: dict
    created_at: UTCDateTime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int | None
    actor: str
    action: str
    detail: dict
    created_at: UTCDateTime


class CaseDetail(BaseModel):
    case: CaseOut
    policy: dict
    findings: list[FindingOut]
    steps: list[StepOut]
    actions: list[str]
    provider_reply_available: bool


class ApproveRequest(BaseModel):
    summary: str | None = None


class ResourceOut(BaseModel):
    id: str
    resource_type: str
    summary: str
    text: str
    raw: dict
