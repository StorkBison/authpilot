"""FHIR R4 access: a local store over synthetic bundles, or any FHIR server over HTTP.

Also renders resources to compact text - the form both the agent and the evidence viewer
use, and the text that cited quotes are checked against (grounding).
"""

import base64
import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Protocol

import httpx

CLINICAL_TYPES = (
    "Condition",
    "Observation",
    "MedicationRequest",
    "Procedure",
    "AllergyIntolerance",
    "DocumentReference",
)


class FhirStore(Protocol):
    def patient(self, patient_id: str) -> dict | None: ...
    def resources(self, patient_id: str) -> list[dict]: ...
    def add(self, patient_id: str, resource: dict) -> None: ...


class LocalFhirStore:
    """Reads `<data_dir>/<patient-id>.json` bundles; additions go to an overlay directory."""

    def __init__(self, data_dir: Path, overlay_dir: Path | None = None):
        self.data_dir = data_dir
        self.overlay_dir = overlay_dir

    def _bundle(self, patient_id: str) -> list[dict]:
        path = self.data_dir / f"{patient_id}.json"
        if not path.exists():
            return []
        bundle = json.loads(path.read_text(encoding="utf-8"))
        entries = [e["resource"] for e in bundle.get("entry", [])]
        if self.overlay_dir and (overlay := self.overlay_dir / f"{patient_id}.json").exists():
            entries += json.loads(overlay.read_text(encoding="utf-8"))
        return entries

    def patient(self, patient_id: str) -> dict | None:
        return next((r for r in self._bundle(patient_id) if r["resourceType"] == "Patient"), None)

    def resources(self, patient_id: str) -> list[dict]:
        return [r for r in self._bundle(patient_id) if r["resourceType"] != "Patient"]

    def add(self, patient_id: str, resource: dict) -> None:
        if not self.overlay_dir:
            raise RuntimeError("Local store is read-only (no overlay directory configured)")
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        path = self.overlay_dir / f"{patient_id}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        existing.append(resource)
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def reset_overlay(self) -> None:
        if self.overlay_dir and self.overlay_dir.exists():
            for f in self.overlay_dir.glob("*.json"):
                f.unlink()


class HttpFhirStore:
    """Minimal client for a FHIR R4 server (e.g. HAPI FHIR loaded with the same bundles)."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Accept": "application/fhir+json"},
        )

    def patient(self, patient_id: str) -> dict | None:
        res = self.client.get(f"/Patient/{patient_id}")
        return res.json() if res.status_code == 200 else None

    def resources(self, patient_id: str) -> list[dict]:
        out: list[dict] = []
        for rtype in CLINICAL_TYPES:
            url: str | None = f"/{rtype}?patient={patient_id}&_count=200"
            while url:
                bundle = self.client.get(url).raise_for_status().json()
                out += [e["resource"] for e in bundle.get("entry", [])]
                url = next(
                    (lk["url"] for lk in bundle.get("link", []) if lk["relation"] == "next"), None
                )
        return out

    def add(self, patient_id: str, resource: dict) -> None:
        self.client.post(f"/{resource['resourceType']}", json=resource).raise_for_status()


# --- Field helpers -------------------------------------------------------------


def codings(resource: dict, field: str = "code") -> list[dict]:
    concept = resource.get(field) or {}
    return concept.get("coding", []) if isinstance(concept, dict) else []


def code_text(resource: dict, field: str = "code") -> str:
    concept = resource.get(field) or {}
    if concept.get("text"):
        return concept["text"]
    return next((c.get("display", "") for c in codings(resource, field)), "")


def has_code(resource: dict, prefixes: Iterable[str], field: str = "code") -> bool:
    return any(c.get("code", "").startswith(p) for c in codings(resource, field) for p in prefixes)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def clinical_date(resource: dict) -> date | None:
    rt = resource["resourceType"]
    if rt == "Condition":
        return _parse_date(resource.get("onsetDateTime") or resource.get("recordedDate"))
    if rt == "Observation":
        return _parse_date(resource.get("effectiveDateTime"))
    if rt == "MedicationRequest":
        return _parse_date(resource.get("authoredOn"))
    if rt == "Procedure":
        return _parse_date(resource.get("performedDateTime"))
    if rt == "AllergyIntolerance":
        return _parse_date(resource.get("recordedDate"))
    if rt == "DocumentReference":
        return _parse_date(resource.get("date"))
    return None


def document_text(resource: dict) -> str:
    parts = []
    for content in resource.get("content", []):
        att = content.get("attachment", {})
        if att.get("data"):
            parts.append(base64.b64decode(att["data"]).decode("utf-8", errors="replace"))
    return "\n".join(parts)


def describe(resource: dict) -> str:
    """One-line, human-readable rendering used in tool results and the evidence viewer."""
    rt = resource["resourceType"]
    when = clinical_date(resource)
    when_s = when.isoformat() if when else "undated"
    codes = ", ".join(c.get("code", "") for c in codings(resource, _code_field(rt)))
    name = code_text(resource, _code_field(rt))
    if rt == "Condition":
        status = code_text(resource, "clinicalStatus") or "active"
        return f"Condition [{codes}] {name} - onset {when_s} ({status})"
    if rt == "Observation":
        q = resource.get("valueQuantity", {})
        return f"Observation [{codes}] {name} = {q.get('value')} {q.get('unit', '')} on {when_s}"
    if rt == "MedicationRequest":
        dose = (resource.get("dosageInstruction") or [{}])[0].get("text", "")
        status = resource.get("status", "")
        return f"MedicationRequest [{codes}] {name} - {dose} - authored {when_s} ({status})"
    if rt == "Procedure":
        return f"Procedure [{codes}] {name} - performed {when_s}"
    if rt == "AllergyIntolerance":
        reaction = ", ".join(
            concept.get("text")
            or next((c.get("display", "") for c in concept.get("coding", [])), "")
            for r in resource.get("reaction", [])
            for concept in r.get("manifestation", [])
        )
        return f"AllergyIntolerance {name} - reaction: {reaction or 'n/a'} - recorded {when_s}"
    if rt == "DocumentReference":
        return f"DocumentReference '{resource.get('description', name)}' dated {when_s}"
    return f"{rt} {name} {when_s}"


def full_text(resource: dict) -> str:
    """Everything a quote may legitimately be taken from."""
    text = describe(resource)
    if resource["resourceType"] == "DocumentReference":
        text += "\n" + document_text(resource)
    for note in resource.get("note", []):
        text += "\n" + note.get("text", "")
    return text


def _code_field(resource_type: str) -> str:
    return "medicationCodeableConcept" if resource_type == "MedicationRequest" else "code"


_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def quote_in_resource(quote: str, resource: dict) -> bool:
    q = normalize(quote).strip(" .\"'")
    return bool(q) and q in normalize(full_text(resource))
