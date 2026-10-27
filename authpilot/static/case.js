// Case review screen: criteria vs. evidence, the live agent timeline, and the human actions.
let data = null;
let draftDirty = false;
let lastStepCount = -1;

const ACTIONS = {
  run: ["▶ Run agent", "", "Run the agent on this case"],
  approve: ["✓ Approve & submit to payer", "primary", "Send the draft and cited evidence to Northwind"],
  provider_reply: ["✎ Simulate provider reply", "", "Demo: the ordering provider documents the missing information"],
  check_status: ["⟳ Check payer status", "primary", "Ask the payer for a decision"],
  appeal: ["⚑ Draft appeal with agent", "primary", "The agent re-reviews the chart against the denial reason"],
  withdraw: ["Withdraw request", "danger", "Close the case without submitting"],
};

async function load() {
  data = await api(`/api/cases/${CASE_ID}`);
  render();
}

function render() {
  const { case: c, policy, findings, steps, actions } = data;
  const running = c.status === "running" || (actions.length === 0 && !["approved", "withdrawn", "submitted", "denied"].includes(c.status));

  $("#head").innerHTML = `
    <div>
      <h1>${esc(c.patient_name)} <span class="muted">· ${esc(c.service_name)}</span></h1>
      <div class="meta">
        <span class="chip">Patient <b>${esc(c.patient_id)}</b></span>
        <span class="chip">Ordered by <b>${esc(c.ordering_provider)}</b></span>
        <span class="chip">Requested <b>${esc(c.requested_on)}</b></span>
        <span class="chip">Payer policy <b>${esc(c.policy_id)}</b></span>
        ${c.payer_reference ? `<span class="chip">Payer ref <b>${esc(c.payer_reference)}</b></span>` : ""}
        ${c.mode === "appeal" ? `<span class="tag">appeal</span>` : ""}
      </div>
    </div>
    <div class="right">
      ${pill(STATUS, c.status)}
      ${c.prep_seconds != null ? `<span class="muted small">Prepared by ${esc(c.planner)} in ${c.prep_seconds}s</span>` : ""}
    </div>`;
  $("#policy-name").textContent = `${policy.payer} · ${policy.title}`;

  renderBanner(c);
  renderCriteria(policy, findings, c);
  renderDraft(c, actions);
  renderActions(actions, running);
  renderTimeline(steps, c);
}

function renderBanner(c) {
  const b = (cls, icon, title, body) =>
    `<div class="banner ${cls}"><span class="icon">${icon}</span><div><b>${title}</b>${body}</div></div>`;
  let html = "";
  if (c.status === "needs_info") {
    html = b("warn", "✉", `Question sent to ${esc(c.ordering_provider)}`,
      `<div>${esc(c.clarification)}</div>`);
  } else if (c.status === "needs_review" && c.recommendation === "ready") {
    html = b("info", "☑", "Ready for your review",
      "Check each criterion against its cited evidence, edit the draft if needed, then approve. Nothing is sent to the payer until you do.");
  } else if (c.status === "needs_review" && c.recommendation === "not_supported") {
    html = b("bad", "⛔", "The agent recommends not submitting",
      `<div>${esc(c.summary || "")}</div>`);
  } else if (c.status === "submitted") {
    html = b("info", "⇪", `Submitted to Northwind Health Plan (${esc(c.payer_reference)})`,
      "Awaiting the payer's decision - use Check payer status.");
  } else if (c.status === "approved") {
    html = b("good", "✔", "Approved by the payer", `<div>${esc(c.payer_decision_reason || "")}</div>`);
  } else if (c.status === "denied") {
    html = b("bad", "✖", "Denied by the payer", `<div>${esc(c.payer_decision_reason || c.prior_denial_reason || "")}</div>`);
  } else if (c.status === "error") {
    html = b("bad", "!", "The agent could not finish this case", "See the activity log. A coordinator must handle it manually or re-run the agent.");
  }
  $("#banner").innerHTML = html;
}

function renderCriteria(policy, findings, c) {
  const byId = Object.fromEntries(findings.map((f) => [f.criterion_id, f]));
  $("#criteria").innerHTML = policy.criteria.map((cr) => {
    const f = byId[cr.id];
    const st = f ? f.status : "pending";
    const badge = f
      ? pill({ met: ["Met", "good"], not_met: ["Not met", "bad"], unclear: ["Unclear", "warn"] }, f.status)
      : `<span class="pill">${c.status === "running" ? "Checking…" : "Not reviewed"}</span>`;
    const ev = f && f.evidence.length
      ? `<div class="evidence">${f.evidence.map((e) => `
          <button class="ev" type="button" data-rid="${esc(e.resource_id)}" data-quote="${esc(e.quote)}">
            <span class="src">${esc(e.resource_id.replace(c.patient_id + "-", ""))}</span>
            <span class="q">“${esc(e.quote)}”</span>
          </button>`).join("")}</div>`
      : "";
    return `<div class="crit ${st}">
      <div class="cid">${esc(cr.id)}</div>
      <div>
        <div class="text">${esc(cr.text)}</div>
        <div class="row">${badge}<span class="muted small">If unmet: ${cr.if_unmet === "deny" ? "request does not qualify" : "ask the provider"}</span></div>
        ${f ? `<div class="rationale">${esc(f.rationale)}</div>` : ""}
        ${ev}
      </div>
    </div>`;
  }).join("");
}

function renderDraft(c, actions) {
  const show = !!c.summary && c.recommendation === "ready";
  $("#draft-card").hidden = !show;
  if (!show) return;
  const editable = actions.includes("approve");
  $("#draft-title").textContent = c.mode === "appeal" ? "Appeal letter (draft)" : "Medical-necessity summary (draft)";
  $("#draft-hint").textContent = editable ? "Editable - your changes are saved when you approve" : "As submitted";
  const ta = $("#draft");
  ta.readOnly = !editable;
  if (!draftDirty) ta.value = c.summary;
}

function renderActions(actions, running) {
  const el = $("#actions");
  if (running) {
    el.innerHTML = `<span class="note">The agent is working on this case…</span>`;
    return;
  }
  el.innerHTML = actions.map((a) => {
    const [label, cls, title] = ACTIONS[a];
    return `<button class="btn ${cls}" data-action="${a}" title="${esc(title)}">${label}</button>`;
  }).join("") + (actions.includes("approve") ? `<span class="note">Approving sends the draft and the cited records to the payer.</span>` : "");
}

function renderTimeline(steps, c) {
  const state = $("#agent-state");
  state.className = "pill " + (c.status === "running" ? "info running" : steps.length ? "good" : "");
  state.textContent = c.status === "running" ? "Working" : steps.length ? `${steps.filter((s) => s.kind === "tool_call").length} tool calls` : "Idle";
  if (steps.length === lastStepCount) return;
  lastStepCount = steps.length;
  const tl = $("#timeline");
  if (!steps.length) {
    tl.innerHTML = `<li class="empty-tl">No agent activity yet.</li>`;
    return;
  }
  tl.innerHTML = steps.map((s) => {
    const t = new Date(s.created_at).toLocaleTimeString();
    if (s.kind === "reasoning") {
      return `<li class="reasoning"><div class="kind">Reasoning · ${t}</div><div class="body">${esc(s.content.text)}</div></li>`;
    }
    if (s.kind === "tool_call") {
      return `<li class="tool_call"><div class="kind">Tool call · ${t}</div><span class="tool">${esc(s.tool)}</span>
        <div class="body">${esc(describeArgs(s.tool, s.content.args))}</div></li>`;
    }
    if (s.kind === "tool_result") {
      let pretty = s.content.result;
      try { pretty = JSON.stringify(JSON.parse(pretty), null, 2); } catch {}
      return `<li class="tool_result ${s.content.is_error ? "err" : ""}"><details>
        <summary>${s.content.is_error ? "⚠ rejected by guardrail" : "result"} · ${esc(s.tool)}</summary><pre>${esc(pretty)}</pre></details></li>`;
    }
    if (s.kind === "error") return `<li class="error"><div class="kind">Error</div><div class="body">${esc(s.content.text)}</div></li>`;
    return `<li class="done"><div class="kind">Done · ${t}</div><div class="body">${esc(s.content.text)} (${s.content.seconds}s)</div></li>`;
  }).join("");
  tl.scrollTop = tl.scrollHeight;
}

function describeArgs(tool, a) {
  if (!a) return "";
  switch (tool) {
    case "search_records": return `${a.resource_type}${a.query ? ` · "${a.query}"` : ""}${a.since_days ? ` · last ${a.since_days} days` : ""}`;
    case "read_record": return a.resource_id;
    case "record_finding": return `${a.criterion_id} → ${a.status.replace("_", " ")} · ${a.evidence.length} citation(s)\n${a.rationale}`;
    case "finish": return `${a.recommendation.replace("_", " ")}`;
    case "get_policy": return a.policy_id;
    default: return Object.keys(a).length ? JSON.stringify(a) : "";
  }
}

// --- Interactions -----------------------------------------------------------------

$("#draft").addEventListener("input", () => (draftDirty = true));

$("#actions").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === "withdraw" && !confirm("Withdraw this request without submitting?")) return;
  btn.disabled = true;
  try {
    const path = { run: "run", approve: "approve", provider_reply: "provider-reply",
      check_status: "check-status", appeal: "appeal", withdraw: "withdraw" }[action];
    const body = action === "approve" ? JSON.stringify({ summary: $("#draft").value }) : undefined;
    const res = await api(`/api/cases/${CASE_ID}/${path}`, { method: "POST", body });
    draftDirty = false;
    lastStepCount = -1;
    if (action === "approve") toast(`Submitted to Northwind - reference ${res.payer_reference}`);
    else if (action === "check_status") toast(res.status === "submitted" ? "Still pending at the payer" : `Payer decision: ${res.status}`);
    else if (action === "provider_reply") toast(`Provider added ${res.added} record(s) - agent re-checking the case`);
    else if (action === "run" || action === "appeal") toast("Agent started");
    await load();
  } catch (err) {
    toast(err.message);
    btn.disabled = false;
  }
});

$("#criteria").addEventListener("click", async (e) => {
  const ev = e.target.closest(".ev");
  if (!ev) return;
  const r = await api(`/api/patients/${data.case.patient_id}/records/${ev.dataset.rid}`);
  $("#record-title").textContent = `${r.resource_type} · ${r.id}`;
  $("#record-summary").textContent = r.summary;
  const quote = ev.dataset.quote;
  const text = esc(r.text);
  const q = esc(quote).trim();
  const idx = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
  $("#record-text").innerHTML = idx >= 0
    ? text.slice(0, idx) + "<mark>" + text.slice(idx, idx + q.length) + "</mark>" + text.slice(idx + q.length)
    : text;
  $("#record-raw").textContent = JSON.stringify(r.raw, null, 2);
  $("#record").showModal();
});

load();
setInterval(() => {
  if (!data) return;
  const active = data.case.status === "running" || data.actions.length === 0;
  if (active || document.hasFocus()) load();
}, 1500);
