"""Score a planner against the labelled synthetic cases.

authpilot eval                  # offline rules planner (free, deterministic)
authpilot eval --planner claude # requires ANTHROPIC_API_KEY (costs API credits)
"""

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from authpilot.agent.runner import AgentRunner
from authpilot.config import ROOT, Settings
from authpilot.db import Database
from authpilot.fhir import LocalFhirStore
from authpilot.models import AgentStep
from authpilot.seed import create_case, load_case_specs

REPORT_DIR = ROOT / "var" / "eval"
BASELINE_DIR = ROOT / "data" / "eval_reports"  # committed reports, shown when none ran locally


def run_eval(planner, settings: Settings, limit: int | None = None) -> dict:
    specs = [s for s in load_case_specs() if s["group"] == "eval"][:limit]
    db = Database("sqlite://")
    db.create_all()
    runner = AgentRunner(LocalFhirStore(settings.fhir_data_dir), planner, settings.agent_max_steps)

    rows = []
    started = time.monotonic()
    for spec in specs:
        with db.session() as session:
            case = runner.run(session, create_case(session, spec).id)
            calls = session.scalar(
                select(func.count(AgentStep.id)).where(
                    AgentStep.case_id == case.id, AgentStep.kind == "tool_call"
                )
            )
            got = case.recommendation or "error"
            rows.append(
                {
                    "patient_id": spec["patient_id"],
                    "service_code": spec["service_code"],
                    "scenario": spec["scenario"],
                    "trap": spec.get("trap", False),
                    "expected": spec["label"],
                    "got": got,
                    "correct": got == spec["label"],
                    "seconds": case.prep_seconds,
                    "tool_calls": calls,
                }
            )

    def acc(subset: list[dict]) -> dict:
        n = len(subset)
        ok = sum(r["correct"] for r in subset)
        return {"correct": ok, "total": n, "accuracy": round(ok / n, 3) if n else None}

    by_service = {
        svc: acc([r for r in rows if r["service_code"] == svc])
        for svc in sorted({r["service_code"] for r in rows})
    }
    return {
        "planner": planner.name,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - started, 1),
        "overall": acc(rows),
        "traps": acc([r for r in rows if r["trap"]]),
        "non_traps": acc([r for r in rows if not r["trap"]]),
        "by_service": by_service,
        "cases": rows,
    }


def save_report(report: dict, directory: Path = REPORT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", report["planner"].lower()).strip("-")
    path = directory / f"{slug}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def load_reports(*directories: Path) -> list[dict]:
    """Latest report per planner; local runs take precedence over committed baselines."""
    by_planner: dict[str, dict] = {}
    for directory in directories or (BASELINE_DIR, REPORT_DIR):
        for path in directory.glob("*.json") if directory.exists() else []:
            report = json.loads(path.read_text(encoding="utf-8"))
            by_planner[report["planner"]] = report
    return sorted(by_planner.values(), key=lambda r: r["ran_at"], reverse=True)
