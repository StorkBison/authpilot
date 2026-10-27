from datetime import date

from authpilot import fhir, rules
from authpilot.models import Case
from authpilot.payer import PayerSimulator
from authpilot.policies import load_policies, policy_for_service

ON = date(2026, 10, 20)


def test_policies_load():
    policies = load_policies()
    assert set(policies) == {"NW-IMG-012", "NW-RX-031", "NW-IMG-020"}
    assert policy_for_service("SVC-MRI-KNEE").id == "NW-IMG-020"
    agent_view = policies["NW-RX-031"].for_agent()
    assert "any_of" not in str(agent_view)  # payer rules are not shown to the agent


def test_time_windows(store):
    knee = load_policies()["NW-IMG-020"]
    stale = store.resources("p-eval-23")  # X-ray 120 days old
    assert rules.evaluate_criterion(knee.criterion("K3"), stale, ON).status == "not_met"
    fresh = store.resources("p-eval-21")
    assert rules.evaluate_criterion(knee.criterion("K3"), fresh, ON).status == "met"


def test_deny_if_rules_out(store):
    glp1 = load_policies()["NW-RX-031"]
    result = rules.evaluate_criterion(glp1.criterion("G2"), store.resources("p-eval-15"), ON)
    assert result.status == "not_met" and result.ruled_out and result.action_if_unmet == "deny"


def test_describe_and_document_text(store):
    docs = [r for r in store.resources("p-robert-diaz") if r["resourceType"] == "DocumentReference"]
    assert any("8 sessions" in fhir.document_text(d) for d in docs)
    allergy = {
        "resourceType": "AllergyIntolerance",
        "code": {"text": "metformin"},
        "reaction": [{"manifestation": [{"text": "Diarrhea"}]}],
        "recordedDate": "2026-01-01",
    }
    assert "Diarrhea" in fhir.describe(allergy)


def _case(session, patient_id, policy_id, service):
    case = Case(
        patient_id=patient_id,
        patient_name="x",
        service_code=service,
        service_name="x",
        policy_id=policy_id,
        ordering_provider="x",
        requested_on=ON,
    )
    session.add(case)
    session.commit()
    return case


def test_payer_adjudicates_only_cited_evidence(db, store):
    payer = PayerSimulator(store, decision_after_checks=1)
    with db.session() as s:
        case = _case(s, "p-robert-diaz", "NW-IMG-020", "SVC-MRI-KNEE")
        weak = payer.submit(
            s,
            case,
            {"K1": ["p-robert-diaz-cond-6"], "K3": ["p-robert-diaz-proc-7"]},
            "summary",
            "initial",
        )
        assert weak.status == "pending"
        assert payer.check_status(s, weak.reference).status == "denied"
        assert "K2 not met" in weak.decision_reason

        strong = payer.submit(
            s,
            case,
            {
                "K1": ["p-robert-diaz-cond-6"],
                "K2": ["p-robert-diaz-docu-9"],
                "K3": ["p-robert-diaz-proc-7"],
            },
            "appeal",
            "appeal",
        )
        assert payer.check_status(s, strong.reference).status == "approved"


def test_payer_ignores_records_not_in_chart(db, store):
    payer = PayerSimulator(store, decision_after_checks=0)
    with db.session() as s:
        case = _case(s, "p-james-walker", "NW-IMG-012", "SVC-MRI-LSPINE")
        sub = payer.submit(s, case, {"L1": ["fabricated"], "L2": ["fabricated"]}, "x", "initial")
        assert sub.status == "denied"
