"""The agent's tools. The same tool layer serves the Claude planner and the offline planner.

Guardrails live here, not in the prompt:
- `record_finding` rejects evidence that does not exist in this patient's chart, or a quote
  that does not appear in the cited record (grounding).
- `finish` rejects inconsistent outcomes (e.g. "ready" with an unmet criterion).
- There is deliberately no tool that submits to the payer: only a human can do that.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from authpilot import fhir
from authpilot.models import Case, Finding
from authpilot.policies import Policy


class ToolError(Exception):
    """Returned to the planner as an is_error tool result so it can correct itself."""


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_case",
        "description": "Get the authorization case: patient, requested service, payer policy "
        "id, request date, ordering provider, and - for appeals - the payer's denial reason "
        "and the evidence that was previously submitted.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_policy",
        "description": "Get the payer's coverage policy for the requested service: each "
        "criterion's id and text, and what to do if it is unmet (clarify = ask the provider "
        "for documentation, deny = request does not meet policy).",
        "input_schema": _schema({"policy_id": {"type": "string"}}),
    },
    {
        "name": "search_records",
        "description": "Search the patient's FHIR chart. Returns one line per matching record "
        "with its id, type and date. Use read_record to see a document's full text.",
        "input_schema": _schema(
            {
                "resource_type": {
                    "type": "string",
                    "enum": ["any", *fhir.CLINICAL_TYPES],
                    "description": "Restrict to one FHIR resource type, or 'any'.",
                },
                "query": {
                    "type": "string",
                    "description": "Case-insensitive keywords matched against the record text "
                    "(any word may match). Empty string lists everything.",
                },
                "since_days": {
                    "type": "integer",
                    "description": "Only records dated within this many days before the "
                    "request date. 0 = no limit.",
                },
            }
        ),
    },
    {
        "name": "read_record",
        "description": "Read one chart record in full, including the complete text of clinical "
        "notes and scanned documents.",
        "input_schema": _schema({"resource_id": {"type": "string"}}),
    },
    {
        "name": "record_finding",
        "description": "Record your verdict on one policy criterion. Every piece of evidence "
        "must cite a record id from this patient's chart and a short quote copied verbatim "
        "from that record. Recording a criterion again replaces the earlier finding.",
        "input_schema": _schema(
            {
                "criterion_id": {"type": "string"},
                "status": {"type": "string", "enum": ["met", "not_met", "unclear"]},
                "rationale": {
                    "type": "string",
                    "description": "One or two sentences a payer reviewer can follow.",
                },
                "evidence": {
                    "type": "array",
                    "items": _schema(
                        {
                            "resource_id": {"type": "string"},
                            "quote": {
                                "type": "string",
                                "description": "Verbatim excerpt from the record.",
                            },
                        }
                    ),
                },
            }
        ),
    },
    {
        "name": "finish",
        "description": "Finish the case once every criterion has a finding. recommendation: "
        "'ready' (all criteria met - draft goes to the coordinator for approval), "
        "'needs_info' (documentation missing - ask the provider), or 'not_supported' "
        "(policy not met). summary: for 'ready', the medical-necessity summary (or the appeal "
        "letter in appeal mode) written for the payer's reviewer; otherwise a short "
        "explanation for the coordinator. provider_question: the specific question for the "
        "ordering provider when 'needs_info', else an empty string.",
        "input_schema": _schema(
            {
                "recommendation": {
                    "type": "string",
                    "enum": ["ready", "needs_info", "not_supported"],
                },
                "summary": {"type": "string"},
                "provider_question": {"type": "string"},
            }
        ),
    },
]

TERMINAL_TOOLS = {"finish"}


@dataclass
class ToolContext:
    session: Session
    case: Case
    policy: Policy
    store: fhir.FhirStore
    finished: bool = False
    outcome: dict = field(default_factory=dict)
    _resources: list[dict] | None = None

    @property
    def resources(self) -> list[dict]:
        if self._resources is None:
            self._resources = self.store.resources(self.case.patient_id)
        return self._resources

    @property
    def on(self) -> date:
        return self.case.requested_on

    def by_id(self, resource_id: str) -> dict:
        found = next((r for r in self.resources if r["id"] == resource_id), None)
        if not found:
            raise ToolError(f"No record '{resource_id}' in this patient's chart.")
        return found

    # --- dispatch -----------------------------------------------------------------

    def execute(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool '{name}'.")
        return json.dumps(handler(**args), ensure_ascii=False, default=str)

    # --- tools --------------------------------------------------------------------

    def tool_get_case(self) -> dict:
        c = self.case
        patient = self.store.patient(c.patient_id) or {}
        info = {
            "case_id": c.id,
            "mode": c.mode,
            "patient": {
                "id": c.patient_id,
                "name": c.patient_name,
                "gender": patient.get("gender"),
                "birth_date": patient.get("birthDate"),
            },
            "requested_service": {"code": c.service_code, "name": c.service_name},
            "policy_id": c.policy_id,
            "requested_on": c.requested_on.isoformat(),
            "ordering_provider": c.ordering_provider,
        }
        if c.mode == "appeal":
            info["denial"] = {
                "reason": c.prior_denial_reason,
                "instruction": "Find documentation that addresses the denial reason and "
                "draft an appeal letter in the finish summary.",
            }
        return info

    def tool_get_policy(self, policy_id: str) -> dict:
        if policy_id != self.policy.id:
            raise ToolError(f"This case is governed by policy {self.policy.id}, not {policy_id}.")
        return self.policy.for_agent()

    def tool_search_records(self, resource_type: str, query: str, since_days: int) -> dict:
        words = [w for w in query.lower().split() if w]
        hits = []
        for r in self.resources:
            if resource_type != "any" and r["resourceType"] != resource_type:
                continue
            when = fhir.clinical_date(r)
            if since_days and (not when or (self.on - when).days > since_days):
                continue
            text = fhir.full_text(r).lower()
            if words and not any(w in text for w in words):
                continue
            hits.append(
                {
                    "id": r["id"],
                    "type": r["resourceType"],
                    "date": when.isoformat() if when else None,
                    "text": fhir.describe(r),
                }
            )
        hits.sort(key=lambda h: h["date"] or "", reverse=True)
        return {"count": len(hits), "records": hits[:50]}

    def tool_read_record(self, resource_id: str) -> dict:
        r = self.by_id(resource_id)
        out = {"id": r["id"], "type": r["resourceType"], "summary": fhir.describe(r)}
        if r["resourceType"] == "DocumentReference":
            out["document_text"] = fhir.document_text(r)
        if r.get("note"):
            out["notes"] = [n.get("text", "") for n in r["note"]]
        return out

    def tool_record_finding(
        self, criterion_id: str, status: str, rationale: str, evidence: list[dict]
    ) -> dict:
        if not self.policy.criterion(criterion_id):
            ids = [c.id for c in self.policy.criteria]
            raise ToolError(f"Unknown criterion '{criterion_id}'. Policy criteria: {ids}.")
        if status == "met" and not evidence:
            raise ToolError("A 'met' finding must cite at least one record.")
        for item in evidence:
            record = self.by_id(item["resource_id"])
            if not fhir.quote_in_resource(item["quote"], record):
                raise ToolError(
                    f"Quote not found in {item['resource_id']}. Copy the text verbatim "
                    "from read_record or search_records output."
                )
        self.session.query(Finding).filter_by(
            case_id=self.case.id, criterion_id=criterion_id
        ).delete()
        self.session.add(
            Finding(
                case_id=self.case.id,
                criterion_id=criterion_id,
                status=status,
                rationale=rationale,
                evidence=evidence,
            )
        )
        self.session.commit()
        return {"recorded": criterion_id, "status": status}

    def tool_finish(self, recommendation: str, summary: str, provider_question: str) -> dict:
        findings = {
            f.criterion_id: f.status
            for f in self.session.query(Finding).filter_by(case_id=self.case.id)
        }
        missing = [c.id for c in self.policy.criteria if c.id not in findings]
        if missing:
            raise ToolError(f"Record a finding for every criterion first. Missing: {missing}.")
        unmet = [cid for cid, s in findings.items() if s != "met"]
        if recommendation == "ready" and unmet:
            raise ToolError(f"Cannot be 'ready' while criteria are not met: {unmet}.")
        if recommendation != "ready" and not unmet:
            raise ToolError("All criteria are met - the recommendation should be 'ready'.")
        if recommendation == "needs_info" and not provider_question.strip():
            raise ToolError("Provide the question for the ordering provider.")
        if not summary.strip():
            raise ToolError("Summary must not be empty.")
        self.finished = True
        self.outcome = {
            "recommendation": recommendation,
            "summary": summary.strip(),
            "provider_question": provider_question.strip() or None,
        }
        return {"status": "finished", "recommendation": recommendation}
