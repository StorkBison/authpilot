"""HttpFhirStore against a mocked FHIR server, and the transaction loader."""

import importlib.util
import json

import httpx

from authpilot.config import ROOT
from authpilot.fhir import HttpFhirStore

BUNDLE = json.loads((ROOT / "data" / "fhir" / "p-robert-diaz.json").read_text(encoding="utf-8"))
RESOURCES = [e["resource"] for e in BUNDLE["entry"]]


def fake_server(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/fhir/Patient/p-robert-diaz":
        return httpx.Response(200, json=RESOURCES[0])
    rtype = path.rsplit("/", 1)[-1]
    if request.url.params.get("patient") == "p-robert-diaz":
        matches = [r for r in RESOURCES if r["resourceType"] == rtype]
        # Page the first type to exercise `next` links.
        if rtype == "Observation" and "page" not in request.url.params:
            return httpx.Response(200, json={
                "entry": [{"resource": r} for r in matches[:1]],
                "link": [{"relation": "next", "url": "/Observation?patient=p-robert-diaz&page=2"}],
            })  # fmt: skip
        if rtype == "Observation":
            matches = matches[1:]
        return httpx.Response(200, json={"entry": [{"resource": r} for r in matches]})
    return httpx.Response(404)


def test_http_store_reads_all_clinical_types():
    store = HttpFhirStore("http://fhir.test/fhir")
    store.client = httpx.Client(
        base_url="http://fhir.test/fhir", transport=httpx.MockTransport(fake_server)
    )
    assert store.patient("p-robert-diaz")["id"] == "p-robert-diaz"
    got = {r["id"] for r in store.resources("p-robert-diaz")}
    assert got == {r["id"] for r in RESOURCES[1:]}


def test_loader_builds_put_transactions():
    spec = importlib.util.spec_from_file_location(
        "loader", ROOT / "scripts" / "load_fhir_server.py"
    )
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    tx = loader.to_transaction(BUNDLE)
    assert tx["type"] == "transaction"
    assert tx["entry"][0]["request"] == {"method": "PUT", "url": "Patient/p-robert-diaz"}
    assert len(tx["entry"]) == len(BUNDLE["entry"])
