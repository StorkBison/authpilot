"""Drives a planner, executes its tool calls, and records every step for the UI and audit."""

import logging
import time
import uuid

from sqlalchemy.orm import Session

from authpilot.agent.tools import ToolContext, ToolError
from authpilot.audit import record_audit
from authpilot.fhir import FhirStore
from authpilot.models import AgentStep, Case, CaseStatus, Finding
from authpilot.policies import load_policies

log = logging.getLogger(__name__)

OUTCOME_STATUS = {
    "ready": CaseStatus.NEEDS_REVIEW,
    "not_supported": CaseStatus.NEEDS_REVIEW,
    "needs_info": CaseStatus.NEEDS_INFO,
}


class AgentRunner:
    def __init__(self, store: FhirStore, planner, max_steps: int = 30):
        self.store = store
        self.planner = planner
        self.max_steps = max_steps

    def run(self, session: Session, case_id: int) -> Case:
        case = session.get(Case, case_id)
        policy = load_policies()[case.policy_id]
        run_id = uuid.uuid4().hex[:12]
        started = time.monotonic()

        session.query(Finding).filter_by(case_id=case.id).delete()
        case.status = CaseStatus.RUNNING
        case.planner = self.planner.name
        case.recommendation = case.summary = case.clarification = None
        session.commit()
        record_audit(session, case.id, f"agent:{self.planner.name}", "agent_started",
                     {"run_id": run_id, "mode": case.mode})  # fmt: skip

        ctx = ToolContext(session=session, case=case, policy=policy, store=self.store)

        def step(kind: str, tool: str | None = None, **content) -> None:
            session.add(AgentStep(case_id=case.id, run_id=run_id, kind=kind, tool=tool,
                                  content=content))  # fmt: skip
            session.commit()

        gen = self.planner.run(ctx)
        reply = None
        calls = 0
        try:
            while not ctx.finished:
                try:
                    event = gen.send(reply)
                except StopIteration:
                    break
                reply = None
                if event[0] == "reasoning":
                    step("reasoning", text=event[1])
                    continue
                _, name, args = event
                calls += 1
                if calls > self.max_steps:
                    raise RuntimeError(f"Step limit reached ({self.max_steps} tool calls)")
                step("tool_call", name, args=args)
                try:
                    result, is_error = ctx.execute(name, args), False
                except ToolError as exc:
                    result, is_error = f"Error: {exc}", True
                except TypeError as exc:  # wrong/missing arguments
                    result, is_error = f"Error: invalid arguments: {exc}", True
                step("tool_result", name, result=result, is_error=is_error)
                reply = (result, is_error)
            if not ctx.finished:
                raise RuntimeError("Planner stopped without finishing the case")
        except Exception as exc:
            log.exception("Agent run failed for case %s", case.id)
            session.rollback()
            case.status = CaseStatus.ERROR
            step("error", text=f"{type(exc).__name__}: {exc}")
            record_audit(session, case.id, f"agent:{self.planner.name}", "agent_failed",
                         {"run_id": run_id, "error": str(exc)})  # fmt: skip
            session.commit()
            return case
        finally:
            gen.close()

        outcome = ctx.outcome
        case.recommendation = outcome["recommendation"]
        case.summary = outcome["summary"]
        case.clarification = outcome["provider_question"]
        case.status = OUTCOME_STATUS[outcome["recommendation"]]
        case.prep_seconds = round(time.monotonic() - started, 1)
        step("done", text=f"Recommendation: {case.recommendation}", seconds=case.prep_seconds)
        record_audit(
            session, case.id, f"agent:{self.planner.name}", "agent_finished",
            {"run_id": run_id, "recommendation": case.recommendation, "tool_calls": calls,
             "seconds": case.prep_seconds},
        )  # fmt: skip
        session.commit()
        return case
