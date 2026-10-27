"""Deterministic evaluation of policy criteria against FHIR resources.

Used by the simulated payer to adjudicate submissions, and by the offline planner. It is
deliberately literal (codes, dates, keywords) - which is exactly where an LLM reading the
chart in context does better (negations, plans vs. completed therapy, nuance in notes).
"""

import operator
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from authpilot import fhir
from authpilot.policies import Criterion

OPS = {">=": operator.ge, ">": operator.gt, "<": operator.lt, "<=": operator.le}


@dataclass
class Match:
    resource: dict
    quote: str

    @property
    def resource_id(self) -> str:
        return self.resource["id"]


def _age_days(resource: dict, on: date) -> int | None:
    when = fhir.clinical_date(resource)
    return (on - when).days if when else None


def _within(resource: dict, on: date, check: dict[str, Any]) -> bool:
    age = _age_days(resource, on)
    if age is None or age < 0:  # undated, or after the request date
        return False
    if "min_age_days" in check and age < check["min_age_days"]:
        return False
    return not ("within_days" in check and age > check["within_days"])


def evaluate_check(check: dict[str, Any], resources: list[dict], on: date) -> list[Match]:
    kind = check["type"]
    out: list[Match] = []
    for r in resources:
        rt = r["resourceType"]
        if kind == "condition" and rt == "Condition":
            if fhir.has_code(r, check["codes"]) and _within(r, on, check):
                out.append(Match(r, fhir.describe(r)))
        elif kind == "observation" and rt == "Observation":
            if not fhir.has_code(r, [check["loinc"]]) or not _within(r, on, check):
                continue
            value = r.get("valueQuantity", {}).get("value")
            if value is not None and OPS[check["op"]](float(value), float(check["value"])):
                out.append(Match(r, fhir.describe(r)))
        elif kind == "medication" and rt == "MedicationRequest":
            field = "medicationCodeableConcept"
            if fhir.has_code(r, check["rxnorm"], field) and _within(r, on, check):
                out.append(Match(r, fhir.describe(r)))
        elif kind == "procedure" and rt == "Procedure":
            text = fhir.code_text(r).lower()
            by_code = fhir.has_code(r, check.get("codes", []))
            by_text = any(t in text for t in check.get("text_any", []))
            if (by_code or by_text) and _within(r, on, check):
                out.append(Match(r, fhir.describe(r)))
        elif kind == "intolerance" and rt == "AllergyIntolerance":
            name = fhir.code_text(r).lower()
            if any(s in name for s in check["substances"]):
                out.append(Match(r, fhir.describe(r)))
        elif kind == "document" and rt == "DocumentReference":
            if not _within(r, on, check):
                continue
            sentences = _sentences_with(fhir.document_text(r), check["any_terms"])
            if sentences:
                # Prefer a concrete sentence ("completed 8 sessions...") over a heading.
                best = next((x for x in sentences if any(ch.isdigit() for ch in x)), sentences[0])
                out.append(Match(r, best))
    if kind == "procedure" and len(out) < check.get("min_count", 1):
        return []
    return out


def _sentences_with(text: str, terms: list[str]) -> list[str]:
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]
    low_terms = [t.lower() for t in terms]
    return [x.rstrip(".") for x in sentences if any(t in x.lower() for t in low_terms)]


@dataclass
class CriterionResult:
    status: str  # met | not_met
    matches: list[Match]
    ruled_out: list[Match]

    @property
    def action_if_unmet(self) -> str:
        return "deny" if self.ruled_out else "clarify"


def rank_matches(matches: list[Match]) -> list[Match]:
    """Most useful citations first: concrete (has numbers), then most recent."""
    return sorted(
        matches,
        key=lambda m: (
            not any(ch.isdigit() for ch in m.quote),
            -(fhir.clinical_date(m.resource) or date.min).toordinal(),
        ),
    )


def evaluate_criterion(criterion: Criterion, resources: list[dict], on: date) -> CriterionResult:
    matches = [m for check in criterion.any_of for m in evaluate_check(check, resources, on)]
    ruled_out = [m for check in criterion.deny_if for m in evaluate_check(check, resources, on)]
    status = "met" if matches and not ruled_out else "not_met"
    return CriterionResult(status, matches, ruled_out)


def recommendation(results: dict[str, CriterionResult], criteria: list[Criterion]) -> str:
    """ready | needs_info | not_supported, from per-criterion results and policy guidance."""
    unmet = [c for c in criteria if results[c.id].status != "met"]
    if not unmet:
        return "ready"
    deny = any(c.if_unmet == "deny" or results[c.id].ruled_out for c in unmet)
    return "not_supported" if deny else "needs_info"
