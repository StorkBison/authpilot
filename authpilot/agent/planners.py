"""Planners decide which tool to call next. Both are generators that yield events and
receive tool results back via `send()`:

    ("reasoning", text)               -> runner logs it, sends back None
    ("tool_call", name, args)         -> runner executes it, sends back (result_text, is_error)

`ClaudePlanner` runs a Claude tool-use loop. `ScriptedPlanner` is a deterministic offline
baseline built on the rule engine - it makes the app work without an API key and gives the
eval something to compare Claude against.
"""

import logging
from collections.abc import Generator
from typing import Any

import anthropic

from authpilot import rules
from authpilot.agent.tools import TOOL_DEFINITIONS, ToolContext

log = logging.getLogger(__name__)

Event = tuple
PlannerGen = Generator[Event, Any, None]

SYSTEM_PROMPT = """\
You are AuthPilot, a prior-authorization assistant working for a clinic's authorization \
coordinator. For one case at a time you check the payer's policy against the patient's \
chart, record a finding for every criterion, and prepare the request. A human coordinator \
reviews your work and decides whether to submit it; you never submit anything yourself.

How to work:
1. Call get_case, then get_policy.
2. For each criterion, search the chart and read the records that could support or refute \
it - including clinical notes and scanned outside records, which often hold evidence that \
structured data lacks.
3. Record a finding per criterion with record_finding. Cite records by id with short quotes \
copied verbatim from them. Prefer the most direct evidence; two or three citations is plenty.
4. Call finish with your recommendation.

Judging evidence - read it the way a careful payer reviewer would:
- Therapy that was only recommended, planned, discussed, declined, or ordered and then \
cancelled was not received. Do not count it as completed.
- Respect the time windows relative to the request date (e.g. "at least 6 weeks", "within \
90 days"). Use the most recent result when a policy asks for a current value.
- A result written in a clinical note counts as documentation even if there is no \
structured record for it.
- Never assume facts that the chart does not state.

Outcomes:
- ready: every criterion is met. The summary is a concise medical-necessity statement for \
the payer's reviewer: the request, then each criterion with its evidence and dates.
- needs_info: a criterion whose policy action is "clarify" lacks documentation. Ask the \
ordering provider one specific question that would resolve it.
- not_supported: a criterion whose policy action is "deny" is not met, or the evidence \
positively rules a criterion out. Explain why for the coordinator.
- In appeal mode, the summary is an appeal letter that quotes the denial reason and points \
to the specific documentation that answers it.
"""


class ClaudePlanner:
    def __init__(self, client: anthropic.Anthropic, model: str, effort: str = "medium"):
        self.client = client
        self.model = model
        self.effort = effort
        self.name = f"claude:{model}"
        self.tools = [{**t, "strict": True} for t in TOOL_DEFINITIONS]

    def run(self, ctx: ToolContext) -> PlannerGen:
        task = (
            f"Process prior-authorization case {ctx.case.id} "
            f"({'appeal of a denial' if ctx.case.mode == 'appeal' else 'new request'})."
        )
        messages: list[dict] = [{"role": "user", "content": task}]
        nudges = 0
        while not ctx.finished:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                raise RuntimeError(f"Model declined the request ({response._request_id})")
            if response.stop_reason == "max_tokens":
                raise RuntimeError("Model response truncated (max_tokens)")

            # Append the full content (incl. thinking blocks) - the history stays append-only.
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    yield ("reasoning", block.thinking)
                elif block.type == "text" and block.text.strip():
                    yield ("reasoning", block.text)
                elif block.type == "tool_use":
                    result, is_error = yield ("tool_call", block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                            **({"is_error": True} if is_error else {}),
                        }
                    )
            if ctx.finished:
                return
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            elif response.stop_reason == "pause_turn":
                continue
            else:
                nudges += 1
                if nudges > 2:
                    raise RuntimeError("Model stopped without calling finish")
                messages.append(
                    {
                        "role": "user",
                        "content": "Record any missing findings, then call finish.",
                    }
                )


class ScriptedPlanner:
    """Deterministic baseline: literal rules over codes, dates and keywords."""

    name = "offline-rules"

    def run(self, ctx: ToolContext) -> PlannerGen:
        yield ("reasoning", "Offline planner (no API key): evaluating each criterion with "
               "deterministic rules over codes, dates and keywords.")  # fmt: skip
        yield ("tool_call", "get_case", {})
        yield ("tool_call", "get_policy", {"policy_id": ctx.policy.id})
        yield (
            "tool_call",
            "search_records",
            {"resource_type": "any", "query": "", "since_days": 0},
        )

        results = {}
        for criterion in ctx.policy.criteria:
            result = rules.evaluate_criterion(criterion, ctx.resources, ctx.on)
            results[criterion.id] = result
            cited = rules.rank_matches(result.ruled_out or result.matches)[:2]
            for m in cited:
                if m.resource["resourceType"] == "DocumentReference":
                    yield ("tool_call", "read_record", {"resource_id": m.resource_id})
            if result.status == "met":
                rationale = f"Matched {len(result.matches)} record(s) satisfying the policy rule."
            elif result.ruled_out:
                rationale = "A record positively rules this criterion out."
            else:
                rationale = "No record in the chart satisfies this criterion."
            yield (
                "tool_call",
                "record_finding",
                {
                    "criterion_id": criterion.id,
                    "status": result.status,
                    "rationale": rationale,
                    "evidence": [{"resource_id": m.resource_id, "quote": m.quote} for m in cited],
                },
            )

        recommendation = rules.recommendation(results, ctx.policy.criteria)
        unmet = [c for c in ctx.policy.criteria if results[c.id].status != "met"]
        question = ""
        if recommendation == "needs_info":
            question = "Please document the following for this request: " + " ".join(
                f"[{c.id}] {c.text}" for c in unmet
            )
        yield (
            "tool_call",
            "finish",
            {
                "recommendation": recommendation,
                "summary": _template_summary(ctx, results, recommendation),
                "provider_question": question,
            },
        )


def _template_summary(ctx: ToolContext, results: dict, recommendation: str) -> str:
    c = ctx.case
    lines = []
    if c.mode == "appeal":
        lines.append(
            f"Re: appeal of denied request for {c.service_name} - {c.patient_name}.\n"
            f"Denial reason: {c.prior_denial_reason}\n"
            "The documentation below addresses the denial:"
        )
    else:
        lines.append(
            f"Request: {c.service_name} for {c.patient_name}, ordered by "
            f"{c.ordering_provider} on {c.requested_on.isoformat()} (policy {ctx.policy.id})."
        )
    for criterion in ctx.policy.criteria:
        r = results[criterion.id]
        evidence = "; ".join(m.quote for m in (r.matches or r.ruled_out)[:2]) or "no documentation"
        lines.append(f"- {criterion.id} {r.status.replace('_', ' ')}: {evidence}")
    if recommendation != "ready":
        lines.append(f"Recommendation: {recommendation.replace('_', ' ')}.")
    return "\n".join(lines)


def build_planner(api_key: str | None, model: str):
    if api_key:
        return ClaudePlanner(anthropic.Anthropic(api_key=api_key), model)
    log.warning("ANTHROPIC_API_KEY not set - using the offline rules planner")
    return ScriptedPlanner()
