import time

import pytest
from fastapi.testclient import TestClient

from authpilot.agent.planners import ScriptedPlanner
from authpilot.api import create_app
from authpilot.config import ROOT, Settings
from authpilot.db import Database
from authpilot.fhir import LocalFhirStore
from authpilot.seed import create_case, load_case_specs

DATA = ROOT / "data" / "fhir"


@pytest.fixture
def store():
    return LocalFhirStore(DATA)  # read-only; the app adds chart records via its database


@pytest.fixture
def db():
    database = Database("sqlite://")
    database.create_all()
    return database


@pytest.fixture
def spec():
    by_patient = {s["patient_id"]: s for s in load_case_specs()}
    return lambda patient_id: by_patient[patient_id]


@pytest.fixture
def make_case(db, spec):
    def _make(patient_id: str):
        with db.session() as s:
            return create_case(s, spec(patient_id)).id

    return _make


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        payer_decision_after_checks=1,
    )
    app = create_app(settings, planner=ScriptedPlanner())
    with TestClient(app) as c:
        yield c


def wait_for(client, case_id: int, timeout: float = 10) -> dict:
    """Poll until the background agent run has finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/cases/{case_id}").json()
        if detail["case"]["status"] != "running" and detail["actions"]:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} still running")


def case_id_for(client, patient_id: str) -> int:
    return next(c["id"] for c in client.get("/api/cases").json() if c["patient_id"] == patient_id)
