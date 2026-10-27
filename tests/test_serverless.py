"""Behaviour on serverless platforms (Vercel): inline runs, shared chart additions, run cap."""

from fastapi.testclient import TestClient

from authpilot.agent.planners import ScriptedPlanner
from authpilot.api import create_app
from authpilot.config import Settings
from tests.conftest import case_id_for


def serverless_app(db_url: str, **overrides):
    settings = Settings(_env_file=None, serverless=True, database_url=db_url, **overrides)
    return create_app(settings, planner=ScriptedPlanner())


def test_defaults_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    s = Settings(_env_file=None)
    assert s.serverless and s.database_url == "sqlite:////tmp/authpilot.db" and s.ephemeral_database
    neon = Settings(_env_file=None, database_url="postgres://u:p@host/db?sslmode=require")
    assert neon.database_url.startswith("postgresql+psycopg://") and not neon.ephemeral_database


def test_agent_runs_inline_and_reports_result(tmp_path):
    with TestClient(serverless_app(f"sqlite:///{tmp_path / 'a.db'}")) as c:
        cid = case_id_for(c, "p-james-walker")
        res = c.post(f"/api/cases/{cid}/run").json()
        assert res["status"] == "needs_review"  # finished before the response returned
        assert c.get(f"/api/cases/{cid}").json()["case"]["recommendation"] == "ready"
        assert c.get("/health").json()["serverless"] is True


def test_chart_additions_are_shared_across_instances(tmp_path):
    """Provider replies live in the database, so a second instance sees them."""
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    with TestClient(serverless_app(url)) as first:
        cid = case_id_for(first, "p-linda-park")
        assert first.post(f"/api/cases/{cid}/run").json()["status"] == "needs_info"
        assert first.post(f"/api/cases/{cid}/provider-reply").json()["status"] == "needs_review"
    with TestClient(serverless_app(url)) as second:
        rec = second.get("/api/patients/p-linda-park/records/p-linda-park-reply-3")
        assert rec.status_code == 200 and "GI intolerance" in rec.json()["text"]


def test_hourly_run_cap(tmp_path):
    app = serverless_app(f"sqlite:///{tmp_path / 'cap.db'}", max_agent_runs_per_hour=1)
    with TestClient(app) as c:
        james, linda = case_id_for(c, "p-james-walker"), case_id_for(c, "p-linda-park")
        assert c.post(f"/api/cases/{james}/run").status_code == 202
        blocked = c.post(f"/api/cases/{linda}/run")
        assert blocked.status_code == 429 and "per hour" in blocked.json()["detail"]


def test_ephemeral_banner(tmp_path):
    # Serverless + SQLite means per-instance storage: the UI warns that data may reset.
    with TestClient(serverless_app(f"sqlite:///{tmp_path / 'e.db'}")) as c:
        assert "may reset at any time" in c.get("/").text


def test_eval_page_shows_committed_baseline(client):
    reports = client.get("/api/eval").json()
    assert any(r["planner"] == "offline-rules" for r in reports)
