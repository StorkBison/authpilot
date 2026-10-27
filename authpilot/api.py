import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from authpilot import __version__, fhir
from authpilot.agent.planners import build_planner
from authpilot.agent.runner import AgentRunner
from authpilot.audit import record_audit
from authpilot.config import Settings, get_settings
from authpilot.db import Database
from authpilot.evaluation import load_reports
from authpilot.models import AgentStep, AuditEvent, Case, CaseStatus, Finding
from authpilot.payer import PayerSimulator
from authpilot.policies import load_policies
from authpilot.schemas import (
    ApproveRequest,
    AuditOut,
    CaseDetail,
    CaseOut,
    FindingOut,
    ResourceOut,
    StepOut,
)
from authpilot.seed import provider_reply_resources, seed

log = logging.getLogger(__name__)
HERE = Path(__file__).parent


def build_store(settings: Settings, db: Database) -> fhir.FhirStore:
    if settings.fhir_base_url:
        return fhir.HttpFhirStore(settings.fhir_base_url)
    return fhir.LocalFhirStore(settings.fhir_data_dir, db=db)


def available_actions(case: Case, has_reply: bool) -> list[str]:
    s = case.status
    actions = []
    if s in (CaseStatus.NEW, CaseStatus.ERROR, CaseStatus.NEEDS_REVIEW, CaseStatus.NEEDS_INFO):
        actions.append("run")
    if s == CaseStatus.NEEDS_REVIEW and case.recommendation == "ready":
        actions.append("approve")
    if s == CaseStatus.NEEDS_INFO and has_reply:
        actions.append("provider_reply")
    if s == CaseStatus.SUBMITTED:
        actions.append("check_status")
    if s == CaseStatus.DENIED:
        actions.append("appeal")
    if s in (CaseStatus.NEEDS_REVIEW, CaseStatus.NEEDS_INFO, CaseStatus.NEW, CaseStatus.ERROR):
        actions.append("withdraw")
    return actions


def create_app(
    settings: Settings | None = None,
    store: fhir.FhirStore | None = None,
    planner=None,
    seed_on_start: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_url)
    store = store or build_store(settings, db)
    planner = planner or build_planner(settings.anthropic_api_key, settings.claude_model)
    runner = AgentRunner(store, planner, settings.agent_max_steps)
    payer = PayerSimulator(store, settings.payer_decision_after_checks)
    user = f"user:{settings.coordinator_name}"
    running: set[int] = set()
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.create_all()
        with db.session() as s:
            if seed_on_start and not s.scalar(select(func.count(Case.id))):
                seed(s)
        yield

    app = FastAPI(
        title="AuthPilot",
        version=__version__,
        description="Prior-authorization agent with a human in the loop (synthetic data only).",
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.store = store
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    DB = Annotated[Session, Depends(db.session_dependency)]

    def get_case(session: Session, case_id: int) -> Case:
        case = session.get(Case, case_id)
        if not case:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
        return case

    def require(case: Case, action: str) -> None:
        has_reply = bool(provider_reply_resources(case))
        if action not in available_actions(case, has_reply) or case.id in running:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"'{action}' is not available while case is {case.status}"
            )

    def enforce_run_cap(session: Session) -> None:
        cap = settings.max_agent_runs_per_hour
        if not cap:
            return
        since = datetime.now(UTC) - timedelta(hours=1)
        recent = session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "agent_started", AuditEvent.created_at >= since
            )
        )
        if recent >= cap:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"This demo allows {cap} agent runs per hour - please try again later.",
                headers={"Retry-After": "600"},
            )

    async def launch_agent(case_id: int) -> str:
        """Background task locally; inline on serverless, where work can't outlive a response."""
        running.add(case_id)

        def work():
            with db.session() as s:
                return runner.run(s, case_id).status

        async def go():
            try:
                return await asyncio.to_thread(work)
            finally:
                running.discard(case_id)

        if settings.serverless:
            return await go()
        task = asyncio.create_task(go())
        background.add(task)
        task.add_done_callback(background.discard)
        return "started"

    # --- Pages ----------------------------------------------------------------------

    def page(request: Request, name: str, **ctx):
        ctx |= {
            "version": __version__,
            "planner": planner.name,
            "coordinator": settings.coordinator_name,
            "ephemeral": settings.ephemeral_database,
        }
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def queue_page(request: Request):
        return page(request, "queue.html")

    @app.get("/cases/{case_id}", response_class=HTMLResponse, include_in_schema=False)
    def case_page(request: Request, case_id: int):
        return page(request, "case.html", case_id=case_id)

    @app.get("/audit", response_class=HTMLResponse, include_in_schema=False)
    def audit_page(request: Request):
        return page(request, "audit.html")

    @app.get("/eval", response_class=HTMLResponse, include_in_schema=False)
    def eval_page(request: Request):
        return page(request, "eval.html", reports=load_reports())

    # --- API ------------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    def health(session: DB):
        session.execute(select(1))
        return {
            "status": "ok",
            "version": __version__,
            "planner": planner.name,
            "serverless": settings.serverless,
            "database": settings.database_url.split(":", 1)[0].split("+", 1)[0],
        }

    @app.get("/api/cases", response_model=list[CaseOut], tags=["cases"])
    def list_cases(session: DB):
        return session.scalars(select(Case).order_by(Case.updated_at.desc())).all()

    @app.get("/api/cases/{case_id}", response_model=CaseDetail, tags=["cases"])
    def case_detail(case_id: int, session: DB):
        case = get_case(session, case_id)
        latest_run = session.scalar(
            select(AgentStep.run_id).where(AgentStep.case_id == case_id)
            .order_by(AgentStep.id.desc()).limit(1)
        )  # fmt: skip
        steps = session.scalars(
            select(AgentStep).where(AgentStep.case_id == case_id, AgentStep.run_id == latest_run)
            .order_by(AgentStep.id)
        ).all() if latest_run else []  # fmt: skip
        findings = session.scalars(
            select(Finding).where(Finding.case_id == case_id).order_by(Finding.criterion_id)
        ).all()
        has_reply = bool(provider_reply_resources(case))
        actions = [] if case.id in running else available_actions(case, has_reply)
        return CaseDetail(
            case=CaseOut.model_validate(case),
            policy=load_policies()[case.policy_id].for_agent(),
            findings=[FindingOut.model_validate(f) for f in findings],
            steps=[StepOut.model_validate(s) for s in steps],
            actions=actions,
            provider_reply_available=has_reply,
        )

    @app.get(
        "/api/patients/{patient_id}/records/{resource_id}",
        response_model=ResourceOut,
        tags=["fhir"],
    )
    def get_record(patient_id: str, resource_id: str):
        record = next((r for r in store.resources(patient_id) if r["id"] == resource_id), None)
        if not record:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
        return ResourceOut(
            id=record["id"],
            resource_type=record["resourceType"],
            summary=fhir.describe(record),
            text=fhir.document_text(record)
            if record["resourceType"] == "DocumentReference"
            else "",
            raw=record,
        )

    @app.post("/api/cases/{case_id}/run", status_code=202, tags=["agent"])
    async def run_agent(case_id: int, session: DB):
        case = get_case(session, case_id)
        require(case, "run")
        enforce_run_cap(session)
        record_audit(session, case.id, user, "agent_requested", {"mode": case.mode})
        return {"status": await launch_agent(case.id)}

    @app.post("/api/cases/{case_id}/appeal", status_code=202, tags=["agent"])
    async def appeal(case_id: int, session: DB):
        case = get_case(session, case_id)
        require(case, "appeal")
        enforce_run_cap(session)
        case.mode = "appeal"
        session.commit()
        record_audit(
            session, case.id, user, "appeal_requested", {"denial": case.prior_denial_reason}
        )
        return {"status": await launch_agent(case.id)}

    @app.post("/api/cases/{case_id}/approve", response_model=CaseOut, tags=["review"])
    def approve(case_id: int, body: ApproveRequest, session: DB):
        """The human gate: only this endpoint sends anything to the payer."""
        case = get_case(session, case_id)
        require(case, "approve")
        if body.summary and body.summary.strip() != (case.summary or "").strip():
            record_audit(session, case.id, user, "summary_edited", {})
            case.summary = body.summary.strip()
        findings = session.scalars(select(Finding).where(Finding.case_id == case.id)).all()
        cited = {f.criterion_id: [e["resource_id"] for e in f.evidence] for f in findings}
        submission = payer.submit(session, case, cited, case.summary, kind=case.mode)
        case.status = CaseStatus.SUBMITTED
        case.payer_reference = submission.reference
        case.payer_decision_reason = None
        session.commit()
        record_audit(session, case.id, user, "approved_and_submitted",
                     {"reference": submission.reference, "kind": case.mode})  # fmt: skip
        if submission.status != "pending":
            apply_decision(session, case, submission)
        return case

    def apply_decision(session: Session, case: Case, submission) -> None:
        case.status = CaseStatus.APPROVED if submission.status == "approved" else CaseStatus.DENIED
        case.payer_decision_reason = submission.decision_reason
        if submission.status == "denied":
            case.prior_denial_reason = submission.decision_reason
        session.commit()
        detail = {"reference": submission.reference, "reason": submission.decision_reason}
        record_audit(session, case.id, "payer", f"payer_{submission.status}", detail)

    @app.post("/api/cases/{case_id}/check-status", response_model=CaseOut, tags=["review"])
    def check_status(case_id: int, session: DB):
        case = get_case(session, case_id)
        require(case, "check_status")
        submission = payer.check_status(session, case.payer_reference)
        record_audit(session, case.id, user, "payer_status_checked",
                     {"reference": case.payer_reference, "status": submission.status})  # fmt: skip
        if submission.status != "pending":
            apply_decision(session, case, submission)
        return case

    @app.post("/api/cases/{case_id}/provider-reply", status_code=202, tags=["review"])
    async def provider_reply(case_id: int, session: DB):
        """Demo stand-in for the provider updating the chart in the EHR."""
        case = get_case(session, case_id)
        require(case, "provider_reply")
        enforce_run_cap(session)
        added = provider_reply_resources(case)
        existing = {r["id"] for r in store.resources(case.patient_id)}
        for resource in added:
            if resource["id"] not in existing:
                store.add(case.patient_id, resource)
        case.status = CaseStatus.NEW
        session.commit()
        record_audit(session, case.id, f"provider:{case.ordering_provider}", "chart_updated",
                     {"resources": [r["id"] for r in added]})  # fmt: skip
        return {"status": await launch_agent(case.id), "added": len(added)}

    @app.post("/api/cases/{case_id}/withdraw", response_model=CaseOut, tags=["review"])
    def withdraw(case_id: int, session: DB):
        case = get_case(session, case_id)
        require(case, "withdraw")
        case.status = CaseStatus.WITHDRAWN
        session.commit()
        record_audit(session, case.id, user, "withdrawn", {"recommendation": case.recommendation})
        return case

    @app.get("/api/audit", response_model=list[AuditOut], tags=["audit"])
    def audit(session: DB, case_id: int | None = None, limit: int = 200):
        stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 1000))
        if case_id is not None:
            stmt = stmt.where(AuditEvent.case_id == case_id)
        return session.scalars(stmt).all()

    @app.get("/api/eval", tags=["eval"])
    def eval_reports():
        return load_reports()

    @app.post("/api/demo/reset", tags=["ops"])
    def reset_demo(session: DB):
        if running:
            raise HTTPException(status.HTTP_409_CONFLICT, "An agent run is in progress")
        session.close()
        db.drop_all()
        db.create_all()  # also clears chart additions made during the demo
        with db.session() as s:
            n = len(seed(s))
        return {"status": "reset", "cases": n}

    return app
