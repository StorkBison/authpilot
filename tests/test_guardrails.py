"""Tool-level guardrails that keep the agent grounded and honest."""

import json

import pytest

from authpilot.agent.tools import ToolContext, ToolError
from authpilot.models import Case
from authpilot.policies import load_policies


@pytest.fixture
def ctx(db, store, make_case):
    cid = make_case("p-james-walker")
    session = db.session()
    case = session.get(Case, cid)
    return ToolContext(
        session=session, case=case, policy=load_policies()[case.policy_id], store=store
    )


def test_rejects_unknown_record(ctx):
    with pytest.raises(ToolError, match="No record"):
        ctx.execute(
            "record_finding",
            {
                "criterion_id": "L1",
                "status": "met",
                "rationale": "x",
                "evidence": [{"resource_id": "made-up-1", "quote": "x"}],
            },
        )


def test_rejects_quote_not_in_record(ctx):
    rid = "p-james-walker-cond-6"
    with pytest.raises(ToolError, match="Quote not found"):
        ctx.execute(
            "record_finding",
            {
                "criterion_id": "L1",
                "status": "met",
                "rationale": "x",
                "evidence": [{"resource_id": rid, "quote": "onset 2019"}],
            },
        )


def test_accepts_verbatim_quote_case_and_space_insensitive(ctx):
    rid = "p-james-walker-cond-6"
    out = json.loads(
        ctx.execute(
            "record_finding",
            {
                "criterion_id": "L1",
                "status": "met",
                "rationale": "8 weeks of symptoms",
                "evidence": [{"resource_id": rid, "quote": "low back pain   WITH sciatica"}],
            },
        )
    )
    assert out == {"recorded": "L1", "status": "met"}


def test_met_requires_evidence_and_known_criterion(ctx):
    with pytest.raises(ToolError, match="at least one"):
        ctx.execute(
            "record_finding",
            {"criterion_id": "L1", "status": "met", "rationale": "x", "evidence": []},
        )
    with pytest.raises(ToolError, match="Unknown criterion"):
        ctx.execute(
            "record_finding",
            {"criterion_id": "Z9", "status": "not_met", "rationale": "x", "evidence": []},
        )


def test_finish_requires_all_findings_and_consistency(ctx):
    finish = {"recommendation": "ready", "summary": "s", "provider_question": ""}
    with pytest.raises(ToolError, match="Missing"):
        ctx.execute("finish", finish)
    ctx.execute(
        "record_finding",
        {
            "criterion_id": "L1",
            "status": "met",
            "rationale": "r",
            "evidence": [{"resource_id": "p-james-walker-cond-6", "quote": "Low back pain"}],
        },
    )
    ctx.execute(
        "record_finding",
        {"criterion_id": "L2", "status": "not_met", "rationale": "r", "evidence": []},
    )
    with pytest.raises(ToolError, match="not met"):
        ctx.execute("finish", finish)
    with pytest.raises(ToolError, match="question"):
        ctx.execute("finish", {**finish, "recommendation": "needs_info"})
    ctx.execute("finish", {**finish, "recommendation": "needs_info", "provider_question": "PT?"})
    assert ctx.finished and ctx.outcome["recommendation"] == "needs_info"


def test_policy_mismatch_and_search(ctx):
    with pytest.raises(ToolError, match="governed by"):
        ctx.execute("get_policy", {"policy_id": "NW-RX-031"})
    res = json.loads(
        ctx.execute(
            "search_records", {"resource_type": "Procedure", "query": "therapy", "since_days": 0}
        )
    )
    assert res["count"] == 6
    recent = json.loads(
        ctx.execute("search_records", {"resource_type": "any", "query": "", "since_days": 7})
    )
    assert all(r["date"] >= "2026-10-13" for r in recent["records"])


def test_there_is_no_submit_tool():
    from authpilot.agent.tools import TOOL_DEFINITIONS

    assert not any("submit" in t["name"] for t in TOOL_DEFINITIONS)
