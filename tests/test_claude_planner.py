"""The Claude tool-use loop, driven by a scripted fake client (no network)."""

from types import SimpleNamespace

from authpilot.agent.planners import ClaudePlanner, ScriptedPlanner
from authpilot.agent.runner import AgentRunner
from authpilot.evaluation import run_eval
from authpilot.models import AgentStep, Case


def tool_use(i, name, args):
    return SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args)


def response(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, _request_id="req_test")


class FakeClient:
    """Replays a fixed conversation and records every request it receives."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Snapshot: the planner keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def scripted_conversation():
    cond, doc = "p-james-walker-cond-6", "p-james-walker-docu-15"
    return [
        response(
            SimpleNamespace(type="thinking", thinking="Start with the case and policy."),
            tool_use(1, "get_case", {}),
            tool_use(2, "get_policy", {"policy_id": "NW-IMG-012"}),
        ),
        response(tool_use(3, "read_record", {"resource_id": doc})),
        # Invented quote -> guardrail rejects it; the model must correct itself.
        response(
            tool_use(
                4,
                "record_finding",
                {
                    "criterion_id": "L1",
                    "status": "met",
                    "rationale": "r",
                    "evidence": [{"resource_id": cond, "quote": "pain for 12 weeks"}],
                },
            )
        ),
        response(
            tool_use(
                5,
                "record_finding",
                {
                    "criterion_id": "L1",
                    "status": "met",
                    "rationale": "Onset 8 weeks before the request.",
                    "evidence": [{"resource_id": cond, "quote": "Low back pain with sciatica"}],
                },
            ),
            tool_use(
                6,
                "record_finding",
                {
                    "criterion_id": "L2",
                    "status": "met",
                    "rationale": "6 PT sessions and 8 weeks of naproxen.",
                    "evidence": [
                        {
                            "resource_id": doc,
                            "quote": "after 6 physical therapy sessions and 8 weeks of naproxen",
                        }
                    ],
                },
            ),
        ),
        response(
            SimpleNamespace(type="text", text="Both criteria are met."),
            tool_use(
                7,
                "finish",
                {"recommendation": "ready", "summary": "MRI justified.", "provider_question": ""},
            ),
        ),
    ]


def test_claude_loop_with_guardrail_correction(db, store, make_case):
    client = FakeClient(scripted_conversation())
    planner = ClaudePlanner(client, "claude-opus-5-5")
    cid = make_case("p-james-walker")
    with db.session() as s:
        case = AgentRunner(store, planner).run(s, cid)
        assert case.status == "needs_review" and case.recommendation == "ready"
        assert case.planner == "claude:claude-opus-5-5"
        steps = s.query(AgentStep).filter_by(case_id=cid).all()
        assert any(
            st.kind == "reasoning" and "case and policy" in st.content["text"] for st in steps
        )
        errors = [st for st in steps if st.kind == "tool_result" and st.content["is_error"]]
        assert len(errors) == 1 and "Quote not found" in errors[0].content["result"]

    # Both parallel tool results went back in ONE user message, error flagged.
    second = client.requests[1]["messages"][-1]
    assert second["role"] == "user" and len(second["content"]) == 2
    rejected = client.requests[3]["messages"][-1]["content"][0]
    assert rejected["is_error"] is True

    # Request shape: strict tools, adaptive thinking with summaries, no forced tool_choice.
    req = client.requests[0]
    assert all(t["strict"] for t in req["tools"])
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "tool_choice" not in req
    assert req["model"] == "claude-opus-5-5"


def test_refusal_marks_case_for_human(db, store, make_case):
    client = FakeClient([response(stop="refusal")])
    cid = make_case("p-james-walker")
    with db.session() as s:
        case = AgentRunner(store, ClaudePlanner(client, "m")).run(s, cid)
        assert case.status == "error"


def test_step_limit(db, store, make_case):
    loop = [response(tool_use(i, "get_case", {})) for i in range(50)]
    cid = make_case("p-james-walker")
    with db.session() as s:
        case = AgentRunner(store, ClaudePlanner(FakeClient(loop), "m"), max_steps=5).run(s, cid)
        assert case.status == "error"
        assert (
            "Step limit"
            in s.query(AgentStep).filter_by(case_id=cid, kind="error").one().content["text"]
        )


def test_offline_eval_baseline():
    from authpilot.config import Settings

    report = run_eval(ScriptedPlanner(), Settings(_env_file=None))
    assert report["overall"]["total"] == 30
    assert report["non_traps"]["accuracy"] == 1.0  # rules handle the straightforward cases
    assert report["traps"]["correct"] == 0  # ...and miss every nuance trap, by design


def test_case_model_roundtrip(db, make_case):
    cid = make_case("p-linda-park")
    with db.session() as s:
        assert s.get(Case, cid).policy_id == "NW-RX-031"
