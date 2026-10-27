// Shared helpers for all pages.
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), 4500);
}

function ago(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

const STATUS = {
  new: ["New", ""],
  running: ["Agent working…", "info running"],
  needs_review: ["Awaiting your review", "accent"],
  needs_info: ["Waiting on provider", "warn"],
  submitted: ["Submitted to payer", "info"],
  approved: ["Approved", "good"],
  denied: ["Denied", "bad"],
  withdrawn: ["Withdrawn", ""],
  error: ["Agent error", "bad"],
};
const RECO = {
  ready: ["Ready to submit", "good"],
  needs_info: ["Needs documentation", "warn"],
  not_supported: ["Does not meet policy", "bad"],
};

function pill(map, key) {
  if (!key) return `<span class="muted">-</span>`;
  const [label, cls] = map[key] || [key, ""];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}
