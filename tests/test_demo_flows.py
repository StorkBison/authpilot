"""The three demo stories, end to end through the HTTP API (offline planner)."""

from tests.conftest import case_id_for, wait_for


def test_seeded_queue(client):
    cases = client.get("/api/cases").json()
    assert {c["patient_id"] for c in cases} == {"p-james-walker", "p-linda-park", "p-robert-diaz"}
    robert = next(c for c in cases if c["patient_id"] == "p-robert-diaz")
    assert robert["status"] == "denied"


def test_story_1_ready_approve_submit_approved(client):
    cid = case_id_for(client, "p-james-walker")
    assert client.post(f"/api/cases/{cid}/run").status_code == 202
    detail = wait_for(client, cid)

    assert detail["case"]["status"] == "needs_review"
    assert detail["case"]["recommendation"] == "ready"
    assert {f["criterion_id"]: f["status"] for f in detail["findings"]} == {
        "L1": "met",
        "L2": "met",
    }
    assert all(f["evidence"] for f in detail["findings"])
    assert "approve" in detail["actions"]

    edited = detail["case"]["summary"] + "\nReviewed by coordinator."
    res = client.post(f"/api/cases/{cid}/approve", json={"summary": edited}).json()
    assert res["status"] == "submitted" and res["payer_reference"].startswith("NW-PA-")
    assert res["summary"].endswith("Reviewed by coordinator.")

    decided = client.post(f"/api/cases/{cid}/check-status").json()
    assert decided["status"] == "approved"

    actions = [e["action"] for e in client.get(f"/api/audit?case_id={cid}").json()]
    for expected in (
        "agent_finished",
        "summary_edited",
        "approved_and_submitted",
        "payer_approved",
    ):
        assert expected in actions


def test_story_2_needs_info_then_provider_reply(client):
    cid = case_id_for(client, "p-linda-park")
    client.post(f"/api/cases/{cid}/run")
    detail = wait_for(client, cid)

    assert detail["case"]["status"] == "needs_info"
    assert "metformin" in detail["case"]["clarification"].lower()
    assert "approve" not in detail["actions"]
    assert "provider_reply" in detail["actions"]

    assert client.post(f"/api/cases/{cid}/provider-reply").status_code == 202
    detail = wait_for(client, cid)
    assert detail["case"]["recommendation"] == "ready"
    g3 = next(f for f in detail["findings"] if f["criterion_id"] == "G3")
    assert any("reply" in e["resource_id"] for e in g3["evidence"])

    client.post(f"/api/cases/{cid}/approve", json={})
    assert client.post(f"/api/cases/{cid}/check-status").json()["status"] == "approved"


def test_story_3_denial_appeal_approved(client):
    cid = case_id_for(client, "p-robert-diaz")
    detail = client.get(f"/api/cases/{cid}").json()
    assert detail["actions"] == ["appeal"]

    client.post(f"/api/cases/{cid}/appeal")
    detail = wait_for(client, cid)
    assert detail["case"]["mode"] == "appeal"
    assert detail["case"]["recommendation"] == "ready"
    k2 = next(f for f in detail["findings"] if f["criterion_id"] == "K2")
    assert k2["status"] == "met"
    assert any("physical therapy" in e["quote"].lower() for e in k2["evidence"])
    assert "Denial reason" in detail["case"]["summary"]

    client.post(f"/api/cases/{cid}/approve", json={})
    assert client.post(f"/api/cases/{cid}/check-status").json()["status"] == "approved"


def test_actions_are_gated(client):
    cid = case_id_for(client, "p-james-walker")
    assert client.post(f"/api/cases/{cid}/approve", json={}).status_code == 409  # not reviewed yet
    assert client.post(f"/api/cases/{cid}/check-status").status_code == 409
    assert client.post("/api/cases/999/run").status_code == 404


def test_record_viewer(client):
    rec = client.get("/api/patients/p-robert-diaz/records/p-robert-diaz-docu-9").json()
    assert rec["resource_type"] == "DocumentReference"
    assert "8 sessions of physical therapy" in rec["text"]
    assert client.get("/api/patients/p-robert-diaz/records/nope").status_code == 404


def test_pages_render(client):
    for path in ("/", "/audit", "/eval", f"/cases/{case_id_for(client, 'p-linda-park')}"):
        assert client.get(path).status_code == 200


def test_reset_demo(client):
    cid = case_id_for(client, "p-james-walker")
    client.post(f"/api/cases/{cid}/run")
    wait_for(client, cid)
    assert client.post("/api/demo/reset").json()["cases"] == 3
    assert all(c["status"] in ("new", "denied") for c in client.get("/api/cases").json())
