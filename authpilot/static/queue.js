async function loadQueue() {
  const cases = await api("/api/cases");
  const count = (st) => cases.filter((c) => c.status === st).length;
  const prepped = cases.filter((c) => c.prep_seconds != null);
  const avg = prepped.length
    ? (prepped.reduce((a, c) => a + c.prep_seconds, 0) / prepped.length).toFixed(1) + "s"
    : "–";
  $("#kpis").innerHTML = [
    ["Awaiting your review", count("needs_review")],
    ["Waiting on provider", count("needs_info")],
    ["With payer", count("submitted")],
    ["Approved", count("approved")],
    ["Avg. agent prep time", avg],
  ].map(([l, v]) => `<div class="kpi"><span class="label">${l}</span><span class="value">${v}</span></div>`).join("");

  $("#queue tbody").innerHTML = cases.map((c) => `
    <tr class="link" data-id="${c.id}">
      <td><b>${esc(c.patient_name)}</b><span class="sub">${esc(c.patient_id)}</span></td>
      <td>${esc(c.service_name)}<span class="sub">${esc(c.ordering_provider)} · ${esc(c.requested_on)}</span></td>
      <td class="mono">${esc(c.policy_id)}${c.mode === "appeal" ? ' <span class="tag">appeal</span>' : ""}</td>
      <td>${pill(STATUS, c.status)}</td>
      <td>${pill(RECO, c.recommendation)}</td>
      <td class="num">${c.prep_seconds != null ? c.prep_seconds + "s" : "–"}</td>
      <td class="muted">${ago(c.updated_at)}</td>
    </tr>`).join("") || `<tr><td colspan="7" class="empty">No cases</td></tr>`;
}

$("#queue").addEventListener("click", (e) => {
  const row = e.target.closest("tr[data-id]");
  if (row) location.href = `/cases/${row.dataset.id}`;
});

$("#reset").addEventListener("click", async () => {
  if (!confirm("Reset the demo? All cases, findings and audit events will be reloaded.")) return;
  try {
    const r = await api("/api/demo/reset", { method: "POST" });
    toast(`Demo reset - ${r.cases} cases loaded`);
    loadQueue();
  } catch (e) { toast(e.message); }
});

loadQueue();
setInterval(loadQueue, 3000);
