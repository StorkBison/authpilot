"""Generate synthetic FHIR R4 patient bundles and labelled authorization cases.

    python scripts/generate_patients.py

Writes data/fhir/<patient-id>.json and data/cases.json. All people are fictional.
Every eval case is labelled by construction with the outcome a careful reviewer should
reach; some are deliberately tricky (negations, cancelled orders, evidence only in notes).
Codes are standard where shown (ICD-10-CM, LOINC, RxNorm, SNOMED CT) except the clinic's
local imaging code XR-KNEE.
"""

import base64
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
REQUESTED_ON = date(2026, 10, 20)
PROVIDER = "Dr. Emily Chen"

ICD = "http://hl7.org/fhir/sid/icd-10-cm"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SNOMED = "http://snomed.info/sct"
LOCAL = "http://riverside-clinic.example/codes"


def d(days_before: int) -> str:
    return (REQUESTED_ON - timedelta(days=days_before)).isoformat()


class Chart:
    def __init__(self, pid: str, given: str, family: str, gender: str, birth: str):
        self.pid = pid
        self.n = 0
        self.resources: list[dict] = [
            {
                "resourceType": "Patient",
                "id": pid,
                "name": [{"given": [given], "family": family}],
                "gender": gender,
                "birthDate": birth,
            }
        ]

    def _add(self, rtype: str, body: dict) -> str:
        self.n += 1
        rid = f"{self.pid}-{rtype.lower()[:4]}-{self.n}"
        self.resources.append(
            {"resourceType": rtype, "id": rid, "subject": {"reference": f"Patient/{self.pid}"}}
            | body
        )
        return rid

    def condition(self, code: str, display: str, onset_days: int, status: str = "active"):
        return self._add(
            "Condition",
            {
                "clinicalStatus": {"coding": [{"code": status}], "text": status},
                "code": {"coding": [{"system": ICD, "code": code, "display": display}]},
                "onsetDateTime": d(onset_days),
            },
        )

    def a1c(self, value: float, days: int):
        return self._add(
            "Observation",
            {
                "status": "final",
                "code": {
                    "coding": [{"system": LOINC, "code": "4548-4", "display": "Hemoglobin A1c"}]
                },
                "valueQuantity": {"value": value, "unit": "%"},
                "effectiveDateTime": d(days),
            },
        )

    def vitals(self, days: int):
        return self._add(
            "Observation",
            {
                "status": "final",
                "code": {"coding": [{"system": LOINC, "code": "8480-6", "display": "Systolic BP"}]},
                "valueQuantity": {"value": random.randint(118, 142), "unit": "mm[Hg]"},
                "effectiveDateTime": d(days),
            },
        )

    def med(self, rxnorm: str, name: str, dose: str, days: int, status: str = "active"):
        return self._add(
            "MedicationRequest",
            {
                "status": status,
                "intent": "order",
                "medicationCodeableConcept": {
                    "coding": [{"system": RXNORM, "code": rxnorm, "display": name}]
                },
                "authoredOn": d(days),
                "dosageInstruction": [{"text": dose}],
            },
        )

    def procedure(self, code: str, display: str, days: int, system: str = SNOMED):
        return self._add(
            "Procedure",
            {
                "status": "completed",
                "code": {"coding": [{"system": system, "code": code, "display": display}]},
                "performedDateTime": d(days),
            },
        )

    def pt_sessions(self, count: int, first_days: int):
        for i in range(count):
            self.procedure("91251008", "Physical therapy session", first_days - i * 7)

    def xray_knee(self, days: int):
        return self.procedure(
            "XR-KNEE", "X-ray right knee, weight-bearing, 3 views", days, system=LOCAL
        )

    def intolerance(self, substance: str, reaction: str, days: int):
        return self._add(
            "AllergyIntolerance",
            {
                "type": "intolerance",
                "code": {"text": substance},
                "reaction": [{"manifestation": [{"text": reaction}]}],
                "recordedDate": d(days),
            },
        )

    def note(self, title: str, text: str, days: int):
        return self._add(
            "DocumentReference",
            {
                "status": "current",
                "type": {
                    "coding": [{"system": LOINC, "code": "11506-3", "display": "Progress note"}]
                },
                "description": title,
                "date": d(days),
                "content": [
                    {
                        "attachment": {
                            "contentType": "text/plain",
                            "data": base64.b64encode(text.encode()).decode(),
                        }
                    }
                ],
            },
        )

    def background(self):
        """Unrelated history so the agent has to search, not just read everything."""
        self.condition("I10", "Essential hypertension", random.randint(800, 3000))
        self.med("29046", "lisinopril 10 MG Oral Tablet", "10 mg once daily", 400)
        for days in random.sample(range(20, 400), 3):
            self.vitals(days)

    def bundle(self) -> dict:
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [{"resource": r} for r in self.resources],
        }


def lumbar_dx(
    c: Chart, days: int, code="M54.41", display="Low back pain with sciatica, right side"
):
    return c.condition(code, display, days)


def knee_dx(c: Chart, days: int):
    return c.condition("M25.561", "Pain in right knee", days)


def t2d(c: Chart):
    c.condition("E11.9", "Type 2 diabetes mellitus without complications", 2400)


NAPROXEN = ("7258", "naproxen 500 MG Oral Tablet", "500 mg twice daily with food")
IBUPROFEN = ("5640", "ibuprofen 600 MG Oral Tablet", "600 mg three times daily as needed")
METFORMIN = ("6809", "metformin 500 MG Oral Tablet", "500 mg twice daily")


# --- Showcase cases (the demo story) -------------------------------------------


def showcase() -> list[tuple[Chart, dict]]:
    james = Chart("p-james-walker", "James", "Walker", "male", "1974-03-14")
    james.background()
    lumbar_dx(james, 56)
    james.med(*NAPROXEN, 56)
    james.pt_sessions(6, 49)
    james.note(
        "Office visit - low back pain",
        "52-year-old man with 2 weeks of low back pain radiating down the right leg after "
        "lifting. No saddle anesthesia, no bowel or bladder changes, no weakness. "
        "Plan: naproxen 500 mg BID and referral to physical therapy.",
        56,
    )
    james.note(
        "Follow-up - persistent radicular pain",
        "Persistent right-sided radicular pain after 6 physical therapy sessions and 8 weeks "
        "of naproxen. Positive straight-leg raise on the right. Reflexes symmetric, strength "
        "5/5. No red-flag symptoms. Ordering MRI of the lumbar spine to evaluate for disc "
        "herniation and guide further management.",
        3,
    )

    linda = Chart("p-linda-park", "Linda", "Park", "female", "1965-07-02")
    linda.background()
    t2d(linda)
    linda.a1c(7.6, 210)
    linda.a1c(8.1, 30)
    linda.med("83367", "atorvastatin 20 MG Oral Tablet", "20 mg nightly", 500)
    linda.note(
        "Diabetes follow-up",
        "HbA1c 8.1%, up from 7.6% in the spring despite diet changes. Weight 94 kg. "
        "Discussed GLP-1 receptor agonist therapy for glycaemic control and weight. "
        "Plan: start semaglutide 0.25 mg weekly, titrate monthly.",
        30,
    )

    robert = Chart("p-robert-diaz", "Robert", "Diaz", "male", "1988-11-23")
    robert.background()
    knee_pain_id = knee_dx(robert, 70)
    xray_id = robert.xray_knee(21)
    robert.note(
        "Office visit - right knee pain",
        "Right knee pain for 1 week after recreational soccer. No locking or giving way. "
        "Mild effusion. Plan: activity modification and physical therapy referral.",
        70,
    )
    robert.note(
        "Outside records (scanned) - physical therapy discharge summary",
        "Riverside Physical Therapy. Patient completed 8 sessions of physical therapy "
        "between 2026-08-18 and 2026-10-02 for right knee pain. Home exercise program "
        "established. Minimal improvement in pain with stairs and pivoting. Recommend "
        "follow-up with referring physician for advanced imaging.",
        18,
    )
    robert.note(
        "Follow-up - knee pain not improving",
        "Persistent medial right knee pain despite completing physical therapy. X-ray "
        "unremarkable. Concern for meniscal pathology. Ordering MRI right knee.",
        4,
    )

    return [
        (james, {"service_code": "SVC-MRI-LSPINE", "label": "ready",
                 "scenario": "Showcase 1 - all criteria documented"}),
        (linda, {"service_code": "SVC-GLP1-SEMAGLUTIDE", "label": "needs_info",
                 "scenario": "Showcase 2 - metformin trial not documented"}),
        (robert, {"service_code": "SVC-MRI-KNEE", "label": "ready",
                  "scenario": "Showcase 3 - denied; PT only in scanned outside records",
                  "prior_denial": {
                      "reason": "K2 not met: conservative therapy (physical therapy or NSAIDs) "
                                "is not documented in the submitted records.",
                      "cited": {"K1": [knee_pain_id], "K3": [xray_id]},
                  }}),
    ]  # fmt: skip


# Documentation the ordering provider adds when the agent asks (demo "provider reply").
PROVIDER_REPLIES = {
    "p-linda-park": [
        ("med", METFORMIN, 650, "stopped"),
        ("intolerance", "metformin", "Nausea and diarrhea (GI intolerance)", 495),
        ("note", "Addendum - metformin history",
         "Addendum: patient previously trialed metformin 500 mg BID from January to June 2025. "
         "Discontinued in June 2025 due to persistent GI intolerance (nausea, diarrhea) "
         "despite dose reduction. Not a candidate for re-challenge.", 0),
    ]
}  # fmt: skip


# --- Eval cases --------------------------------------------------------------------

FIRST = [
    "Ava",
    "Noah",
    "Mia",
    "Liam",
    "Zoe",
    "Ethan",
    "Ruby",
    "Owen",
    "Nora",
    "Leo",
    "Ivy",
    "Caleb",
    "Hazel",
    "Miles",
    "Clara",
    "Jude",
    "Elena",
    "Theo",
    "Maya",
    "Felix",
]
LAST = ["Bennett", "Hughes", "Okafor", "Nguyen", "Kowalski", "Ramirez", "Schmidt", "Patel",
        "Larsen", "Moreau", "Tanaka", "Silva", "Brennan", "Haddad", "Novak", "Ortiz"]  # fmt: skip


def eval_cases() -> list[tuple[Chart, dict]]:
    out: list[tuple[Chart, dict]] = []

    def new(i: int) -> Chart:
        c = Chart(f"p-eval-{i:02d}", random.choice(FIRST), random.choice(LAST),
                  random.choice(["male", "female"]), f"{random.randint(1950, 1995)}-0{random.randint(1, 9)}-1{random.randint(0, 9)}")  # fmt: skip
        c.background()
        return c

    def add(c: Chart, service: str, label: str, scenario: str, trap: bool = False):
        out.append((c, {"service_code": service, "label": label, "scenario": scenario,
                        "trap": trap}))  # fmt: skip

    L, G, K = "SVC-MRI-LSPINE", "SVC-GLP1-SEMAGLUTIDE", "SVC-MRI-KNEE"

    # Lumbar spine MRI
    c = new(1)
    lumbar_dx(c, 60)
    c.pt_sessions(5, 52)
    add(c, L, "ready", "PT x5, 8+ weeks")
    c = new(2)
    lumbar_dx(c, 50)
    c.med(*NAPROXEN, 48)
    add(c, L, "ready", "NSAID 7 weeks")
    c = new(3)
    lumbar_dx(c, 14, "M54.16", "Radiculopathy, lumbar region")
    c.condition("M21.371", "Foot drop, right foot", 10)
    c.note(
        "Urgent visit",
        "New right foot drop over 10 days with L5 radicular pain. "
        "Progressive weakness of ankle dorsiflexion 3/5.",
        2,
    )
    add(c, L, "ready", "red flag: progressive foot drop")
    c = new(4)
    lumbar_dx(c, 10, "M54.50", "Low back pain, unspecified")
    c.condition("Z85.3", "Personal history of malignant neoplasm of breast", 1500)
    add(c, L, "ready", "red flag: cancer history")
    c = new(5)
    lumbar_dx(c, 20)
    c.med(*NAPROXEN, 18)
    add(c, L, "not_supported", "only ~3 weeks of symptoms, no red flags")
    c = new(6)
    lumbar_dx(c, 60)
    add(c, L, "needs_info", "8 weeks of pain, no conservative therapy documented")
    c = new(7)
    lumbar_dx(c, 60)
    c.note(
        "Outside PT summary",
        "Completed 6 sessions of physical therapy with partial relief; home exercises continued.",
        12,
    )
    add(c, L, "ready", "PT documented only in a note")
    c = new(8)
    lumbar_dx(c, 60)
    c.note(
        "Office visit",
        "Physical therapy was recommended at the last visit; the patient "
        "has not started yet because of scheduling problems. No medications tried.",
        5,
    )
    add(c, L, "needs_info", "PT recommended but never started", trap=True)
    c = new(9)
    lumbar_dx(c, 60)
    c.med(*IBUPROFEN, 50, status="cancelled")
    add(c, L, "needs_info", "only NSAID order was cancelled", trap=True)
    c = new(10)
    lumbar_dx(c, 30)
    c.pt_sessions(2, 25)
    add(c, L, "not_supported", "~4 weeks of symptoms, no red flags")

    # Semaglutide
    c = new(11)
    t2d(c)
    c.a1c(8.4, 20)
    c.med(*METFORMIN, 400)
    add(c, G, "ready", "metformin + A1c 8.4")
    c = new(12)
    t2d(c)
    c.a1c(7.9, 45)
    c.intolerance("metformin", "Diarrhea", 300)
    add(c, G, "ready", "metformin intolerance recorded")
    c = new(13)
    t2d(c)
    c.a1c(9.0, 10)
    c.note(
        "Diabetes visit",
        "Metformin was stopped after 3 months because of severe diarrhea. A1c 9.0% today.",
        10,
    )
    add(c, G, "ready", "metformin intolerance in a note")
    c = new(14)
    t2d(c)
    c.a1c(8.2, 15)
    add(c, G, "needs_info", "no metformin history")
    c = new(15)
    t2d(c)
    c.a1c(6.6, 30)
    c.med(*METFORMIN, 500)
    add(c, G, "not_supported", "A1c 6.6 at goal")
    c = new(16)
    t2d(c)
    c.a1c(8.5, 200)
    c.med(*METFORMIN, 600)
    add(c, G, "needs_info", "latest A1c is 200 days old")
    c = new(17)
    c.condition("E66.9", "Obesity, unspecified", 1200)
    c.a1c(6.0, 40)
    add(c, G, "not_supported", "no type 2 diabetes diagnosis")
    c = new(18)
    t2d(c)
    c.a1c(8.0, 20)
    c.note(
        "Diabetes visit",
        "Discussed starting metformin today; the patient prefers to "
        "try dietary changes first and will revisit in 3 months.",
        20,
    )
    add(c, G, "needs_info", "metformin discussed but never started", trap=True)
    c = new(19)
    t2d(c)
    c.a1c(7.4, 60)
    c.med(*METFORMIN, 300)
    add(c, G, "ready", "metformin + A1c 7.4")
    c = new(20)
    t2d(c)
    c.med(*METFORMIN, 700)
    c.a1c(8.6, 240)
    c.note(
        "Diabetes visit", "Point-of-care HbA1c today: 8.3%. Adherent to metformin 1000 mg BID.", 6
    )
    add(c, G, "ready", "current A1c recorded only in the visit note", trap=True)

    # Knee MRI
    c = new(21)
    knee_dx(c, 60)
    c.pt_sessions(4, 50)
    c.xray_knee(20)
    add(c, K, "ready", "PT x4 + recent X-ray")
    c = new(22)
    knee_dx(c, 60)
    c.pt_sessions(4, 50)
    add(c, K, "needs_info", "no knee X-ray")
    c = new(23)
    knee_dx(c, 90)
    c.med(*NAPROXEN, 80)
    c.xray_knee(120)
    add(c, K, "needs_info", "X-ray is 120 days old")
    c = new(24)
    c.condition("S83.241A", "Tear of medial meniscus, right knee", 7)
    c.xray_knee(5)
    c.note(
        "Sports medicine", "Twisting injury 1 week ago; knee locks intermittently and gives way.", 5
    )
    add(c, K, "ready", "acute meniscal tear with locking")
    c = new(25)
    knee_dx(c, 14)
    c.xray_knee(10)
    add(c, K, "not_supported", "2 weeks of knee pain, no injury")
    c = new(26)
    knee_dx(c, 70)
    c.xray_knee(30)
    c.note(
        "Scanned PT records",
        "Completed 7 PT sessions (physical therapy) for right knee pain with limited benefit.",
        15,
    )
    add(c, K, "ready", "PT only in scanned records")
    c = new(27)
    knee_dx(c, 60)
    c.xray_knee(15)
    add(c, K, "needs_info", "no conservative therapy documented")
    c = new(28)
    knee_dx(c, 60)
    c.xray_knee(15)
    c.note(
        "Office visit",
        "Patient declined physical therapy referral; prefers to wait. "
        "Not taking any anti-inflammatories.",
        15,
    )
    add(c, K, "needs_info", "patient declined PT", trap=True)
    c = new(29)
    knee_dx(c, 50)
    c.med(*IBUPROFEN, 45)
    c.xray_knee(30)
    add(c, K, "ready", "NSAID 6+ weeks + X-ray")
    c = new(30)
    knee_dx(c, 70)
    c.xray_knee(20)
    for i in range(5):
        c.procedure("PHYSIO-01", "Physiotherapy session (outpatient)", 55 - 7 * i, LOCAL)
    add(c, K, "ready", "PT coded as local 'physiotherapy' procedures", trap=True)

    return out


def main() -> None:
    random.seed(7)
    fhir_dir = OUT / "fhir"
    fhir_dir.mkdir(parents=True, exist_ok=True)
    for old in fhir_dir.glob("*.json"):
        old.unlink()
    cases = []
    for group, items in (("demo", showcase()), ("eval", eval_cases())):
        for chart, meta in items:
            (fhir_dir / f"{chart.pid}.json").write_text(
                json.dumps(chart.bundle(), indent=2), encoding="utf-8"
            )
            p = chart.resources[0]
            name = f"{p['name'][0]['given'][0]} {p['name'][0]['family']}"
            cases.append({"group": group, "patient_id": chart.pid, "patient_name": name,
                          "ordering_provider": PROVIDER,
                          "requested_on": REQUESTED_ON.isoformat(), **meta})  # fmt: skip
    (OUT / "cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    replies = {pid: [list(r) for r in items] for pid, items in PROVIDER_REPLIES.items()}
    (OUT / "provider_replies.json").write_text(json.dumps(replies, indent=2), encoding="utf-8")
    traps = sum(1 for c in cases if c.get("trap"))
    print(f"Wrote {len(cases)} cases ({traps} traps) and {len(cases)} FHIR bundles to {OUT}")


if __name__ == "__main__":
    main()
