"""Load the synthetic bundles into a FHIR R4 server (e.g. HAPI FHIR from docker compose).

    python scripts/load_fhir_server.py http://localhost:8080/fhir

Each collection bundle is posted as a transaction of PUTs, so resource ids are preserved
and the load is idempotent. Then run AuthPilot with FHIR_BASE_URL pointing at the server.
"""

import json
import sys
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "data" / "fhir"


def to_transaction(bundle: dict) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": e["resource"],
                "request": {
                    "method": "PUT",
                    "url": f"{e['resource']['resourceType']}/{e['resource']['id']}",
                },
            }
            for e in bundle["entry"]
        ],
    }


def main(base_url: str) -> None:
    files = sorted(DATA.glob("*.json"))
    with httpx.Client(timeout=60, headers={"Content-Type": "application/fhir+json"}) as client:
        for path in files:
            bundle = json.loads(path.read_text(encoding="utf-8"))
            client.post(base_url.rstrip("/"), json=to_transaction(bundle)).raise_for_status()
            print(f"loaded {path.stem} ({len(bundle['entry'])} resources)")
    print(f"Done: {len(files)} patients -> {base_url}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/fhir")
