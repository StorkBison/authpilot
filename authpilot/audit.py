from sqlalchemy.orm import Session

from authpilot.models import AuditEvent


def record_audit(
    session: Session, case_id: int | None, actor: str, action: str, detail: dict | None = None
) -> None:
    """Append an audit event. Commits so the trail survives later failures."""
    session.add(AuditEvent(case_id=case_id, actor=actor, action=action, detail=detail or {}))
    session.commit()
