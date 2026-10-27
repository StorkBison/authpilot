"""Simulated payer (Northwind Health Plan).

Adjudicates a submission using only the evidence it cites - like a real utilization-review
nurse, it does not go looking through the rest of the chart. Decisions arrive after a
configurable number of status checks, to mimic asynchronous review.
"""

import secrets
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from authpilot import rules
from authpilot.fhir import FhirStore
from authpilot.models import Case, PayerSubmission
from authpilot.policies import load_policies


class PayerSimulator:
    def __init__(self, store: FhirStore, decision_after_checks: int = 1):
        self.store = store
        self.decision_after_checks = decision_after_checks

    def submit(
        self, session: Session, case: Case, cited: dict[str, list[str]], summary: str, kind: str
    ) -> PayerSubmission:
        submission = PayerSubmission(
            reference=f"NW-PA-{secrets.token_hex(3).upper()}",
            case_id=case.id,
            policy_id=case.policy_id,
            kind=kind,
            payload={
                "patient_id": case.patient_id,
                "service_code": case.service_code,
                "requested_on": case.requested_on.isoformat(),
                "criteria": cited,
                "summary": summary,
            },
        )
        session.add(submission)
        session.commit()
        if self.decision_after_checks == 0:
            self._adjudicate(submission)
            session.commit()
        return submission

    def check_status(self, session: Session, reference: str) -> PayerSubmission:
        submission = session.scalar(
            select(PayerSubmission).where(PayerSubmission.reference == reference)
        )
        if submission is None:
            raise KeyError(reference)
        if submission.status == "pending":
            submission.status_checks += 1
            if submission.status_checks >= self.decision_after_checks:
                self._adjudicate(submission)
        session.commit()
        return submission

    def _adjudicate(self, submission: PayerSubmission) -> None:
        policy = load_policies()[submission.policy_id]
        payload = submission.payload
        chart = {r["id"]: r for r in self.store.resources(payload["patient_id"])}
        on = date.fromisoformat(payload["requested_on"])
        unmet = []
        for criterion in policy.criteria:
            cited = [chart[i] for i in payload["criteria"].get(criterion.id, []) if i in chart]
            if rules.evaluate_criterion(criterion, cited, on).status != "met":
                unmet.append(criterion)
        if unmet:
            submission.status = "denied"
            submission.decision_reason = " ".join(
                f"{c.id} not met: the submitted records do not document this criterion "
                f"({c.text.split('.')[0].strip()})." for c in unmet
            )  # fmt: skip
        else:
            submission.status = "approved"
            submission.decision_reason = (
                f"Approved under {policy.id}. Authorization valid for 90 days."
            )
