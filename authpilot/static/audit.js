async function loadAudit() {
  const events = await api("/api/audit?limit=300");
  $("#audit tbody").innerHTML = events.map((e) => {
    const actorCls = e.actor.startsWith("agent") ? "info" : e.actor.startsWith("user") ? "accent"
      : e.actor === "payer" ? "warn" : "";
    const detail = Object.entries(e.detail).map(([k, v]) =>
      `<span class="muted">${esc(k)}:</span> ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join(" · ");
    return `<tr>
      <td class="mono small">${esc(new Date(e.created_at).toLocaleString())}</td>
      <td>${e.case_id ? `<a href="/cases/${e.case_id}">#${e.case_id}</a>` : "–"}</td>
      <td><span class="pill ${actorCls}">${esc(e.actor)}</span></td>
      <td><b>${esc(e.action.replaceAll("_", " "))}</b></td>
      <td class="small">${detail}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">No events</td></tr>`;
}
loadAudit();
setInterval(loadAudit, 4000);
