"""Load the synthetic cases into the database (and reset the demo)."""

import base64
import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from authpilot.audit import record_audit
from authpilot.config import ROOT
from authpilot.models import Case, CaseStatus, PayerSubmission
from authpilot.policies import policy_for_service

CASES_FILE = ROOT / "data" / "cases.json"
REPLIES_FILE = ROOT / "data" / "provider_replies.json"

SERVICE_NAMES = {
    "SVC-MRI-LSPINE": "MRI lumbar spine without contrast",
    "SVC-GLP1-SEMAGLUTIDE": "Semaglutide 0.25 mg weekly injection",
    "SVC-MRI-KNEE": "MRI right knee without contrast",
}


def load_case_specs(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_case(session: Session, spec: dict) -> Case:
    policy = policy_for_service(spec["service_code"])
    case = Case(
        patient_id=spec["patient_id"],
        patient_name=spec["patient_name"],
        service_code=spec["service_code"],
        service_name=SERVICE_NAMES[spec["service_code"]],
        policy_id=policy.id,
        ordering_provider=spec["ordering_provider"],
        requested_on=date.fromisoformat(spec["requested_on"]),
    )
    session.add(case)
    session.flush()

    if denial := spec.get("prior_denial"):
        # A request the clinic submitted by hand before AuthPilot - and that was denied.
        case.status = CaseStatus.DENIED
        case.prior_denial_reason = denial["reason"]
        case.payer_decision_reason = denial["reason"]
        case.payer_reference = "NW-PA-7F31C2"
        session.add(
            PayerSubmission(
                reference=case.payer_reference,
                case_id=case.id,
                policy_id=policy.id,
                kind="initial",
                payload={
                    "patient_id": case.patient_id,
                    "service_code": case.service_code,
                    "requested_on": spec["requested_on"],
                    "criteria": denial["cited"],
                    "summary": "Submitted manually by clinic staff.",
                },
                status="denied",
                decision_reason=denial["reason"],
            )
        )
    session.commit()
    return case


def seed(session: Session, groups: tuple[str, ...] = ("demo",)) -> list[Case]:
    cases = [create_case(session, s) for s in load_case_specs() if s["group"] in groups]
    record_audit(session, None, "system", "demo_seeded", {"cases": len(cases)})
    return cases


def provider_reply_resources(case: Case) -> list[dict]:
    """FHIR resources the ordering provider adds when asked (demo stand-in for the EHR)."""
    replies = json.loads(REPLIES_FILE.read_text(encoding="utf-8")).get(case.patient_id, [])
    out = []
    for i, (kind, *args) in enumerate(replies, start=1):
        rid = f"{case.patient_id}-reply-{i}"
        subject = {"reference": f"Patient/{case.patient_id}"}
        if kind == "med":
            (rxnorm, name, dose), days, status = args
            out.append(
                {
                    "resourceType": "MedicationRequest",
                    "id": rid,
                    "subject": subject,
                    "status": status,
                    "intent": "order",
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": rxnorm,
                                "display": name,
                            }
                        ]
                    },
                    "authoredOn": _before(case, days),
                    "dosageInstruction": [{"text": dose}],
                }
            )
        elif kind == "intolerance":
            substance, reaction, days = args
            out.append(
                {
                    "resourceType": "AllergyIntolerance",
                    "id": rid,
                    "subject": subject,
                    "type": "intolerance",
                    "code": {"text": substance},
                    "reaction": [{"manifestation": [{"text": reaction}]}],
                    "recordedDate": _before(case, days),
                }
            )
        elif kind == "note":
            title, text, days = args
            out.append(
                {
                    "resourceType": "DocumentReference",
                    "id": rid,
                    "subject": subject,
                    "status": "current",
                    "description": title,
                    "date": _before(case, days),
                    "type": {
                        "coding": [
                            {
                                "system": "http://loinc.org",
                                "code": "11506-3",
                                "display": "Progress note",
                            }
                        ]
                    },
                    "content": [
                        {
                            "attachment": {
                                "contentType": "text/plain",
                                "data": base64.b64encode(text.encode()).decode(),
                            }
                        }
                    ],
                }
            )
    return out  # fmt: skip


def _before(case: Case, days: int) -> str:
    return (case.requested_on - timedelta(days=days)).isoformat()
