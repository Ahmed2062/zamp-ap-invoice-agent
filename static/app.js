"use strict";
/* AP Invoice Agent UI - no framework, no build step. All dynamic text goes through esc(). */

const app = document.getElementById("app");
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2 });
const inr = (n) => (n === null || n === undefined || n === "" ? "—" : INR.format(Number(n)));
const inrShort = (n) => {
  n = Number(n || 0);
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
  return INR.format(n);
};
const fmtDT = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const fmtD = (iso) => {
  if (!iso) return "—";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(iso + "T00:00:00") : new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
};
const secs = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 3800);
}

const P = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
const ICONS = {
  upload: `<svg viewBox="0 0 24 24" ${P}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
  chart: `<svg viewBox="0 0 24 24" ${P}><path d="M3 3v18h18"/><path d="M7 16v-5M12 16V8M17 16V7"/></svg>`,
  inbox: `<svg viewBox="0 0 24 24" ${P}><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
  db: `<svg viewBox="0 0 24 24" ${P}><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
  map: `<svg viewBox="0 0 24 24" ${P}><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>`,
  check: `<svg viewBox="0 0 24 24" ${P} stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`,
  x: `<svg viewBox="0 0 24 24" ${P} stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" ${P} stroke-width="3"><line x1="12" y1="6" x2="12" y2="13"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
  minus: `<svg viewBox="0 0 24 24" ${P} stroke-width="3"><line x1="6" y1="12" x2="18" y2="12"/></svg>`,
  spin: `<svg viewBox="0 0 24 24" ${P} stroke-width="3"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>`,
  dot: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.5" fill="currentColor"/></svg>`,
  mail: `<svg viewBox="0 0 24 24" ${P}><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" ${P}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
  doc: `<svg viewBox="0 0 24 24" ${P}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  play: `<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7 4 20 12 7 20 7 4"/></svg>`,
  back: `<svg viewBox="0 0 24 24" ${P}><polyline points="15 18 9 12 15 6"/></svg>`,
  user: `<svg viewBox="0 0 24 24" ${P}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  copy: `<svg viewBox="0 0 24 24" ${P}><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  pay: `<svg viewBox="0 0 24 24" ${P}><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg>`,
  replay: `<svg viewBox="0 0 24 24" ${P}><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`,
};
$$("[data-icon]").forEach((el) => (el.innerHTML = ICONS[el.dataset.icon] || ""));

const DEC = {
  APPROVED: { label: "Approved", long: "Approved for payment", icon: "check" },
  NEEDS_REVIEW: { label: "Needs review", long: "Needs review", icon: "alert" },
  HOLD: { label: "Payment on hold", long: "Payment on hold", icon: "shield" },
  RETURNED: { label: "Returned", long: "Returned to vendor", icon: "mail" },
  REJECTED: { label: "Rejected", long: "Rejected", icon: "x" },
  ERROR: { label: "Error", long: "Processing error", icon: "alert" },
  RUNNING: { label: "Processing", long: "Processing", icon: "spin" },
};
const decKey = (run) => {
  if (run.status === "running") return "RUNNING";
  const d = run.final_decision || run.decision || "ERROR";
  return d === "NEEDS_REVIEW" && run.priority === "High" ? "HOLD" : d;
};
const chip = (key, text) => `<span class="chip ${key}">${esc(text || DEC[key]?.label || key)}</span>`;

const state = { config: null, timers: [], runView: null };
const clearTimers = () => { state.timers.forEach((t) => clearInterval(t)); state.timers = []; state.runView = null; };

/* ------------------------------------------------------------------ boot */
async function boot() {
  try {
    state.config = await api("/api/config");
  } catch (e) {
    app.innerHTML = `<div class="empty">Could not reach the server: ${esc(e.message)}</div>`;
    return;
  }
  const c = state.config;
  $("#org-name").textContent = c.company.name;
  $("#engine-pills").innerHTML =
    `<span class="eng ${c.ocr ? "on" : ""}" title="Scanned invoices are read with OCR">OCR · ${c.ocr ? "Tesseract" : "off"}</span>` +
    `<span class="eng ${c.ai.enabled ? "on" : ""}" title="Independent AI reader cross-checks the rule-based reader">AI reader · ${c.ai.enabled ? esc(c.ai.model) : "off"}</span>`;
  $("#reset-btn").onclick = resetDemo;
  window.addEventListener("hashchange", route);
  refreshQueueBadge();
  setInterval(refreshQueueBadge, 8000);
  route();
}

async function refreshQueueBadge() {
  try {
    const s = await api("/api/stats");
    const b = $("#queue-count");
    b.hidden = !s.open_reviews;
    b.textContent = s.open_reviews;
  } catch (_) { /* ignore */ }
}

async function resetDemo() {
  if (!confirm("Reset demo data? This clears all runs and re-processes the 7 historical invoices.")) return;
  const headers = {};
  if (state.config.reset_protected) {
    const t = prompt("Admin token:");
    if (!t) return;
    headers["X-Admin-Token"] = t;
  }
  try {
    await api("/api/admin/reset", { method: "POST", headers });
    toast("Resetting demo data - re-processing history…");
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 700));
      const h = await api("/api/health");
      if (!h.seeding) break;
    }
    toast("Demo data restored.");
    refreshQueueBadge();
    location.hash = "#/dashboard";
    route();
  } catch (e) { toast(e.message, true); }
}

function route() {
  clearTimers();
  const h = location.hash || "#/new";
  const parts = h.slice(2).split("/");
  const page = parts[0] || "new";
  $$("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (page === "runs" ? "dashboard" : page)));
  window.scrollTo(0, 0);
  if (page === "runs" && parts[1]) return renderRun(Number(parts[1]));
  if (page === "dashboard") return renderDashboard();
  if (page === "queue") return renderQueue();
  if (page === "reference") return renderReference(parts[1] || "pos");
  if (page === "about") return renderAbout();
  return renderNew();
}

/* ------------------------------------------------------------------ new run */
async function renderNew() {
  const c = state.config;
  app.innerHTML = `
    <div class="page-head"><div>
      <h1>Process an invoice</h1>
      <p>Upload a vendor invoice PDF (digital or scanned) or run one of the prepared scenarios. Every stage runs live -
         reading, extraction, validation, vendor and duplicate checks, PO matching - and ends in a decision you can audit.</p>
    </div></div>
    <div class="drop" id="drop">
      <div class="drop-icon">${ICONS.upload}</div>
      <div><h3>Drop an invoice PDF here</h3>
        <p>Max ${c.max_upload_mb} MB · digital PDFs are read exactly, scans go through OCR${c.ai.enabled ? " and the AI reader" : ""}.
           This is a public demo - please don't upload confidential invoices.</p></div>
      <div class="actions">
        <input type="file" id="file" accept="application/pdf,.pdf" hidden>
        <button class="btn primary" id="browse">${ICONS.upload} Choose PDF</button>
      </div>
    </div>
    <div class="section-title">Happy path & headline edge cases</div>
    <div class="samples" id="s-main"><div class="empty">Loading scenarios…</div></div>
    <div class="section-title">More scenarios the process handles</div>
    <div class="samples" id="s-more"></div>`;
  const drop = $("#drop"), file = $("#file");
  $("#browse").onclick = () => file.click();
  file.onchange = () => file.files[0] && upload(file.files[0]);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));

  const samples = await api("/api/samples");
  const card = (s) => {
    const kind = s.group === "Happy path" ? "happy" : s.group.startsWith("Edge") ? "edge" : "";
    return `<div class="card sample ${kind}">
      <div class="thumb" style="background-image:url('/api/samples/${encodeURIComponent(s.id)}/preview.png')"><span class="tag">${esc(s.group)}</span></div>
      <div class="body">
        <h4>${esc(s.title)}</h4>
        <p>${esc(s.description)}</p>
        <div class="meta"><span>${esc(s.vendor)}</span><b>${inr(s.total)}</b></div>
        <div class="row">
          <button class="btn primary sm" data-run="${esc(s.id)}">${ICONS.play} Run</button>
          <a class="btn sm" href="/api/samples/${encodeURIComponent(s.id)}/file" target="_blank" rel="noopener">${ICONS.doc} View PDF</a>
        </div>
      </div></div>`;
  };
  $("#s-main").innerHTML = samples.filter((s) => s.group !== "More scenarios").map(card).join("");
  $("#s-more").innerHTML = samples.filter((s) => s.group === "More scenarios").map(card).join("");
  $$("[data-run]").forEach((b) => (b.onclick = () => startRun({ sampleId: b.dataset.run }, b)));
}

function upload(f) {
  if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") return toast("Please choose a PDF file.", true);
  if (f.size > state.config.max_upload_mb * 1024 * 1024) return toast(`File is larger than ${state.config.max_upload_mb} MB.`, true);
  startRun({ file: f });
}

async function startRun({ file, sampleId }, btn) {
  const fd = new FormData();
  if (file) fd.append("file", file);
  else fd.append("sample_id", sampleId);
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/runs", { method: "POST", body: fd });
    location.hash = `#/runs/${r.run_id}`;
  } catch (e) {
    toast(e.message, true);
    if (btn) btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ run view */
const FIELD_LABELS = {
  vendor_name: "Vendor name", vendor_gstin: "Vendor GSTIN", buyer_gstin: "Buyer GSTIN", invoice_number: "Invoice number",
  invoice_date: "Invoice date", due_date: "Due date", po_number: "PO number", subtotal: "Taxable value", tax_total: "GST",
  total: "Invoice total", "bank.account_number": "Bank account", "bank.ifsc": "IFSC", line_items: "Line items",
};
const MONEY = new Set(["subtotal", "tax_total", "total"]);
const fieldVal = (f, v) => (v === null || v === undefined || v === "" ? '<span class="faint">not found</span>' :
  MONEY.has(f) ? inr(v) : /date/.test(f) ? esc(fmtD(v)) : esc(v));

async function renderRun(id) {
  let run;
  try { run = await api(`/api/runs/${id}`); } catch (e) {
    app.innerHTML = `<div class="empty">Run #${esc(id)} not found. <a href="#/dashboard">Back to dashboard</a></div>`;
    return;
  }
  const view = { id, run, lastSeq: 0, stages: {}, current: null, expanded: new Set(), done: run.status !== "running", review: null };
  state.config.stages.forEach((s) => (view.stages[s.key] = { status: "pending", logs: [], checks: [], summary: "", data: null, ms: null }));
  state.runView = view;
  app.innerHTML = `
    <a class="back" href="#/dashboard">${ICONS.back} Dashboard</a>
    <div class="run-head">
      <div><h1 id="rh-title"></h1><div class="sub" id="rh-sub"></div></div>
      <div style="display:flex;gap:8px">
        <button class="btn sm" id="replay" title="Replay this run's stages">${ICONS.replay} Replay</button>
        <a class="btn sm" href="/api/runs/${id}/file" target="_blank" rel="noopener">${ICONS.doc} Original PDF</a>
      </div>
    </div>
    <div class="progress"><div id="prog" style="width:0%"></div></div>
    <div class="run-grid">
      <div class="timeline" id="timeline"></div>
      <div class="side">
        <div id="decision"></div>
        <div id="outputs"></div>
        <div class="card card-pad" id="facts"></div>
        <div class="card card-pad"><div class="card-head"><h3>Document</h3><a href="/api/runs/${id}/file" target="_blank" rel="noopener" class="small">Open PDF ↗</a></div>
          <div class="doc-prev"><img alt="Invoice preview" src="/api/runs/${id}/preview.png" loading="lazy" onerror="this.parentNode.innerHTML='<div class=empty>No preview</div>'"></div></div>
      </div>
    </div>`;
  $("#timeline").addEventListener("click", (e) => {
    const top = e.target.closest("[data-toggle]");
    if (!top) return;
    const k = top.dataset.toggle;
    view.expanded.has(k) ? view.expanded.delete(k) : view.expanded.add(k);
    drawTimeline(view);
  });
  $("#replay").onclick = () => replay(view);
  drawHead(view);
  const evs = await api(`/api/runs/${id}/events?after=0`);
  evs.events.forEach((ev) => applyEvent(view, ev, !view.done));
  if (view.done) {
    // Completed run: show exceptions expanded, passes collapsed.
    view.expanded.clear();
    Object.entries(view.stages).forEach(([k, s]) => (s.status === "fail" || s.status === "warn") && view.expanded.add(k));
  }
  drawAll(view);
  if (!view.done) {
    const t = setInterval(() => poll(view), 350);
    state.timers.push(t);
  }
}

async function poll(view) {
  if (view.polling || state.runView !== view) return;
  view.polling = true;
  try {
    const r = await api(`/api/runs/${view.id}/events?after=${view.lastSeq}`);
    r.events.forEach((ev) => applyEvent(view, ev, true));
    if (r.events.length) drawTimeline(view);
    if (r.status !== "running" && !view.done) {
      view.done = true;
      clearTimers();
      state.runView = view;
      view.run = await api(`/api/runs/${view.id}`);
      drawAll(view);
      refreshQueueBadge();
    }
  } catch (_) { /* transient */ } finally { view.polling = false; }
}

function applyEvent(view, ev, live) {
  view.lastSeq = Math.max(view.lastSeq, ev.seq);
  const s = ev.stage && view.stages[ev.stage];
  if (ev.type === "stage_start" && s) {
    s.status = "running";
    view.current = ev.stage;
    if (live) view.expanded.add(ev.stage);
  } else if (ev.type === "log" && s) {
    s.logs.push(ev.message);
  } else if (ev.type === "check" && s) {
    s.checks.push(ev.data);
  } else if (ev.type === "stage_end" && s) {
    s.status = ev.status;
    s.severity = ev.data?.severity;
    s.summary = ev.message;
    s.ms = ev.data?.compute_ms;
    s.data = ev.data?.data;
    if (live && (ev.status === "pass" || ev.status === "skip")) view.expanded.delete(ev.stage);
  } else if (ev.type === "run_end") {
    view.current = null;
  }
}

function drawAll(view) {
  drawHead(view);
  drawTimeline(view);
  drawSide(view);
}

function drawHead(view) {
  const r = view.run;
  const key = view.done ? decKey(r) : "RUNNING";
  $("#rh-title").innerHTML = `Run #${r.id} <span class="muted" style="font-weight:500">${esc(r.filename)}</span> ${chip(key, key === "HOLD" ? "Payment on hold" : DEC[key]?.long)}`;
  const f = r.result?.fields || {};
  $("#rh-sub").innerHTML = [
    `Received ${fmtDT(r.created_at)}`, `via ${esc(r.source)}`,
    r.vendor_name ? esc(r.vendor_name) : "", r.total != null ? inr(r.total) : "",
    view.done && r.duration_ms != null ? `processed in ${secs(r.duration_ms)}` : "",
    r.seeded ? '<span class="tag">demo history</span>' : "",
  ].filter(Boolean).join(" · ");
  const n = state.config.stages.length;
  const doneCount = Object.values(view.stages).filter((s) => !["pending", "running"].includes(s.status)).length;
  $("#prog").style.width = `${view.done ? 100 : Math.max(4, (doneCount / n) * 100)}%`;
  void f;
}

const stageIcon = (s) => ({ pending: ICONS.dot, running: ICONS.spin, pass: ICONS.check, warn: ICONS.alert, skip: ICONS.minus,
  error: ICONS.x, fail: s.severity === "reject" ? ICONS.x : s.severity === "hold" ? ICONS.shield : s.severity === "return" ? ICONS.mail : ICONS.alert }[s.status] || ICONS.dot);

function drawTimeline(view) {
  drawHead(view);
  if (!view.done) drawPending(view);
  $("#timeline").innerHTML = state.config.stages.map((meta, i) => {
    const s = view.stages[meta.key];
    const open = view.expanded.has(meta.key) && s.status !== "pending";
    const cls = [s.status, s.severity || "", ["pending", "running"].includes(s.status) ? "" : "done"].join(" ");
    return `<div class="stage ${cls}">
      <div class="dot">${stageIcon(s)}</div>
      <div class="stage-card">
        <div class="stage-top" data-toggle="${meta.key}">
          <div><h4>${i + 1}. ${esc(meta.name)}</h4><div class="desc">${esc(meta.desc)}</div></div>
          <div class="summary">${s.status === "running" ? '<span class="muted">running…</span>' : esc(s.summary || "")}</div>
          <div class="ms">${s.ms != null ? secs(s.ms) : ""}</div>
        </div>
        ${open ? `<div class="stage-body">${stageBody(view, meta.key, s)}</div>` : ""}
      </div></div>`;
  }).join("");
}

function stageBody(view, key, s) {
  const parts = [];
  if (s.logs.length) parts.push(`<div class="logs">${s.logs.map((l) => `<div>${esc(l)}</div>`).join("")}</div>`);
  if (s.checks.length) parts.push(`<div class="checks">${s.checks.map(checkRow).join("")}</div>`);
  const detail = s.data ? stageDetail(view, key, s.data) : "";
  if (detail) parts.push(detail);
  return parts.join("") || '<div class="muted small">Working…</div>';
}

function checkRow(c) {
  const ic = { pass: ICONS.check, warn: "!", skip: ICONS.minus, fail: c.severity === "reject" ? ICONS.x : "!" }[c.status] || "";
  const prec = c.evidence?.precedent;
  return `<div class="check ${esc(c.status)} ${esc(c.severity || "")}">
    <span class="ci">${ic}</span>
    <div><b>${esc(c.label)}</b> <span class="msg">${esc(c.message)}</span>
      ${c.status === "fail" && c.question ? `<div class="q"><b>Question for ${esc(c.queue || "reviewer")}:</b> ${esc(c.question)}</div>` : ""}
      ${prec ? `<div class="prec"><b>Precedent:</b> ${esc(prec.reviewer)} approved "${esc(prec.exception || "a similar exception")}" on ${esc(prec.invoice_number)} (${fmtD(prec.at)}): "${esc(prec.note)}"</div>` : ""}
    </div></div>`;
}

function stageDetail(view, key, d) {
  if (key === "read" && d.text_preview) {
    return `<details class="raw"><summary>Text the process read from the document (${d.text_preview.length} lines)</summary><pre>${esc(d.text_preview.join("\n"))}</pre></details>`;
  }
  if (key === "extract" && d.comparison) {
    const ai = d.comparison.some((r) => r.ai !== null && r.ai !== undefined);
    const src = d.sources || {};
    const rows = d.comparison.filter((r) => r.field !== "line_items").map((r) => {
      const s = src[{ tax_total: "taxes" }[r.field] || r.field.split(".")[0]] || {};
      const conf = r.confidence ?? s.confidence;
      const cc = conf == null ? "" : conf >= 0.85 ? "" : conf >= 0.6 ? "mid" : "low";
      const confHtml = conf == null ? "" : `<span class="conf ${cc}"><span class="bar"><i style="width:${Math.round(conf * 100)}%"></i></span>${Math.round(conf * 100)}%</span>`;
      if (ai) {
        const st = r.status === "agree" ? `<span class="ok-text">agree</span>` : r.status === "conflict" ? `<span class="bad-text">conflict</span>` : esc(r.status);
        return `<tr><td>${esc(FIELD_LABELS[r.field] || r.field)}</td><td>${fieldVal(r.field, r.rules)}</td><td>${fieldVal(r.field, r.ai)}</td><td><b>${fieldVal(r.field, r.value)}</b></td><td>${st}${r.reason ? `<div class="sub">${esc(r.reason)}</div>` : ""}</td></tr>`;
      }
      return `<tr><td>${esc(FIELD_LABELS[r.field] || r.field)}</td><td><b>${fieldVal(r.field, r.value)}</b></td><td>${esc(s.method || "")}${s.evidence ? `<div class="sub">“${esc(s.evidence)}”</div>` : ""}</td><td>${confHtml}</td></tr>`;
    }).join("");
    const head = ai ? "<tr><th>Field</th><th>Rule-based reader</th><th>AI reader</th><th>Final</th><th>Cross-check</th></tr>"
      : "<tr><th>Field</th><th>Value</th><th>How it was found</th><th>Confidence</th></tr>";
    const items = (d.fields?.line_items || []).map((it, i) => `<tr><td>${i + 1}</td><td>${esc(it.description)}</td><td class="num">${it.quantity ?? "—"}</td><td class="num">${it.unit_price != null ? inr(it.unit_price) : "—"}</td><td class="num">${inr(it.amount)}</td></tr>`).join("");
    const taxes = (d.fields?.taxes || []).map((t) => `${esc(t.type)} ${t.rate ?? ""}% ${inr(t.amount)}`).join(" · ");
    return `<div class="tbl-wrap"><table>${head}${rows}</table></div>
      ${items ? `<div class="tbl-wrap"><table><tr><th>#</th><th>Line item</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Amount</th></tr>${items}</table></div>` : ""}
      ${taxes || d.fields?.amount_in_words ? `<div class="small muted">${taxes ? `GST: ${taxes}` : ""}${d.fields?.amount_in_words ? ` · In words: “${esc(d.fields.amount_in_words)}”` : ""}</div>` : ""}`;
  }
  if (key === "po_match" && d.candidates?.length) {
    return `<div class="tbl-wrap"><table><tr><th>Candidate PO</th><th class="num">Score</th><th>Evidence</th><th class="num">Unbilled value</th></tr>${d.candidates.map((c, i) =>
      `<tr><td><b>${esc(c.po_number)}</b>${i === 0 ? ' <span class="tag ok">best</span>' : ""}<div class="sub">raised ${fmtD(c.po_date)}</div></td><td class="num">${c.score.toFixed(2)}</td><td class="small">${Object.entries(c.parts).map(([k, v]) => `${esc(k)} ${Number(v).toFixed(2)}`).join(" · ")}</td><td class="num">${inr(c.remaining_value)}</td></tr>`).join("")}</table></div>`;
  }
  if (key === "reconcile" && (d.lines?.length || d.position?.po_value)) {
    const lines = (d.lines || []).map((l) => {
      const pv = l.price_variance_pct;
      const over = l.billed_after > l.ordered;
      const notRec = l.received != null && l.billed_after > l.received;
      return `<tr><td>${l.po_line}</td><td>${esc(l.description)}</td><td class="num">${inr(l.po_price)}</td>
        <td class="num ${Math.abs(pv) > state.config.policy.price_tolerance_pct ? "bad-text" : ""}">${inr(l.invoice_price)}<div class="sub">${pv > 0 ? "+" : ""}${pv.toFixed(1)}%</div></td>
        <td class="num">${Number(l.ordered).toLocaleString("en-IN")}</td><td class="num">${Number(l.billed_before).toLocaleString("en-IN")}</td>
        <td class="num">${Number(l.this_invoice).toLocaleString("en-IN")}</td>
        <td class="num ${over ? "bad-text" : ""}">${Number(l.billed_after).toLocaleString("en-IN")}</td>
        <td class="num ${notRec ? "bad-text" : ""}">${l.received == null ? '<span class="faint">n/a</span>' : Number(l.received).toLocaleString("en-IN")}</td></tr>`;
    }).join("");
    return `${lines ? `<div class="tbl-wrap"><table><tr><th>PO line</th><th>Item</th><th class="num">PO price</th><th class="num">Billed price</th><th class="num">Ordered</th><th class="num">Billed before</th><th class="num">This invoice</th><th class="num">Billed after</th><th class="num">Received</th></tr>${lines}</table></div>` : ""}
      ${d.position?.po_value ? utilBar(d.position) : ""}`;
  }
  return "";
}

function utilBar(p) {
  const max = Math.max(100, p.util_after);
  const before = Math.min(p.util_before, 100), thisPart = Math.max(0, Math.min(p.util_after, 100) - before), over = Math.max(0, p.util_after - 100);
  return `<div class="util"><div class="small" style="display:flex;justify-content:space-between;margin-bottom:6px"><b>PO utilisation (value)</b><span>${p.util_before.toFixed(0)}% → <b class="${p.util_after > 100 ? "bad-text" : ""}">${p.util_after.toFixed(0)}%</b> of ${inr(p.po_value)}</span></div>
    <div class="util-bar"><div class="before" style="width:${(before / max) * 100}%"></div><div class="this" style="width:${(thisPart / max) * 100}%"></div><div class="over" style="width:${(over / max) * 100}%"></div></div>
    <div class="util-legend"><span style="--c:#a5b4fc">Already invoiced ${inr(p.billed_before)}${p.prior_invoices?.length ? ` (${p.prior_invoices.map((x) => esc(x.invoice_number)).join(", ")})` : ""}</span><span style="--c:#4f46e5">This invoice ${inr(p.this_invoice)}</span>${over ? `<span style="--c:#ef4444">Over PO by ${inr(p.billed_after - p.po_value)}</span>` : ""}</div></div>`;
}

function drawPending(view) {
  const cur = view.current ? state.config.stages.findIndex((s) => s.key === view.current) : -1;
  $("#decision").innerHTML = `<div class="decision pending"><div class="big"><div class="spinner">${ICONS.spin}</div><div><h2>Processing…</h2><div class="muted small">${cur >= 0 ? `Stage ${cur + 1} of ${state.config.stages.length}: ${esc(state.config.stages[cur].name)}` : "Starting"}</div></div></div>
    <p class="explain">The decision appears here when all stages have finished. Each stage on the left shows what it checked and why.</p></div>`;
}

function drawSide(view) {
  const r = view.run;
  if (view.done && !r.result) {
    $("#decision").innerHTML = `<div class="decision ERROR"><div class="big"><div class="ic">${ICONS.alert}</div><div><h2>Processing error</h2></div></div>
      <p class="explain">${esc(r.error || r.headline || "The run stopped unexpectedly. Nothing was booked or paid.")}</p></div>`;
    $("#outputs").innerHTML = "";
    $("#facts").innerHTML = "";
    return;
  }
  if (!view.done || !r.result) {
    drawPending(view);
    $("#outputs").innerHTML = "";
    $("#facts").innerHTML = `<div class="muted small">Extracted facts will appear here.</div>`;
    return;
  }
  const res = r.result, dec = res.decision || {};
  const key = decKey(r);
  const review = res.review;
  const label = review ? DEC[review.final_decision].long : key === "HOLD" ? "Payment on hold" : DEC[key].long;
  $("#decision").innerHTML = `<div class="decision ${key}">
    <div class="big"><div class="ic">${ICONS[DEC[key].icon]}</div><div><h2>${esc(label)}</h2>
      <div class="muted small">${review ? `Decided by ${esc(review.reviewer)} · agent recommended “${esc(DEC[review.agent_decision]?.long || review.agent_decision)}”` : `Decided by the AP agent${dec.queue ? ` · routed to ${esc(dec.queue)}` : ""}${dec.priority === "High" ? " · priority HIGH" : ""}`}</div></div></div>
    <p class="headline">${esc(dec.headline || r.headline)}</p>
    <p class="explain">${esc(dec.explanation || "")}</p>
    ${dec.reasons?.length ? `<ul>${dec.reasons.map((x) => `<li>${esc(x.message)}</li>`).join("")}</ul>` : ""}
    ${dec.notes?.length ? `<details class="raw" style="margin-top:10px"><summary>${dec.notes.length} note(s) recorded on the audit trail</summary><ul>${dec.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>` : ""}
    ${review ? `<div class="output" style="margin-top:12px"><h4>${ICONS.user} Reviewer decision</h4><div class="small">${esc(review.reviewer)} → <b>${esc(DEC[review.final_decision].long)}</b> · ${fmtDT(review.at)}${review.note ? `<div style="margin-top:4px">“${esc(review.note)}”</div>` : ""}</div></div>` : ""}
  </div>`;

  const outs = (dec.outputs || []).map((o) => outputCard(o, r)).join("");
  const actions = dec.next_actions?.length ? `<div class="output"><h4>Next actions</h4><ul style="margin:0;padding-left:18px;display:grid;gap:4px;font-size:13px">${dec.next_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>` : "";
  $("#outputs").innerHTML = `<div class="grid" style="gap:12px">${outs}${actions}</div>`;
  const form = $("#review-form");
  if (form) wireReview(form, view);
  $$("[data-copy]").forEach((b) => (b.onclick = () => {
    navigator.clipboard?.writeText(b.closest(".output").querySelector(".email").textContent);
    toast("Email copied to clipboard");
  }));

  const f = res.fields || {};
  const facts = [
    ["Vendor", res.vendor ? `${esc(res.vendor.name)} <span class="faint">(${esc(res.vendor.vendor_id)})</span>` : esc(f.vendor_name || "—")],
    ["Vendor GSTIN", esc(f.vendor_gstin || "—")],
    ["Invoice no.", esc(f.invoice_number || "—")],
    ["Invoice date", fmtD(f.invoice_date)],
    ["PO", res.po?.po_number ? `${esc(res.po.po_number)}${res.po.inferred ? ' <span class="tag warn">inferred</span>' : ""}` : esc(f.po_number || "—")],
    ["Taxable value", inr(f.subtotal)],
    ["GST", (f.taxes || []).map((t) => `${esc(t.type)} ${inr(t.amount)}`).join(" + ") || "—"],
    ["Total", `<b>${inr(f.total)}</b>`],
    ["Read via", `${r.read_method === "ocr" ? "OCR (scanned)" : r.read_method === "text-layer" ? "Digital text" : esc(r.read_method || "—")}${res.ai?.used ? " + AI reader" : ""}`],
  ];
  $("#facts").innerHTML = `<div class="card-head"><h3>Extracted facts</h3></div><dl class="kv">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function outputCard(o, r) {
  if (o.type === "payment") {
    return `<div class="output"><h4>${ICONS.pay} ${esc(o.title)}</h4><dl class="kv">
      <dt>Pay</dt><dd>${inr(o.amount)}</dd><dt>To</dt><dd>${esc(o.payee)}</dd>
      <dt>Bank</dt><dd>${esc(o.bank)}<div class="faint small">${esc(o.bank_source)}</div></dd>
      <dt>On</dt><dd>${fmtD(o.due_date)}<div class="faint small">${esc(o.due_basis || "")}</div></dd>
      <dt>PO / cost centre</dt><dd>${esc(o.po_number || "—")} · ${esc(o.cost_center || "—")}</dd></dl></div>`;
  }
  if (o.type === "vendor_email") {
    return `<div class="output"><h4>${ICONS.mail} ${esc(o.title)} <button class="btn sm" style="margin-left:auto" data-copy>${ICONS.copy} Copy</button></h4>
      <div class="email-head"><span>To: <b>${esc(o.to)}</b> <span class="faint">(${esc(o.to_source)})</span></span><span>Subject: <b>${esc(o.subject)}</b></span></div>
      <div class="email">${esc(o.body)}</div></div>`;
  }
  if (o.type === "review_task") {
    const open = (r.final_decision || r.decision) === "NEEDS_REVIEW";
    const prec = o.precedent;
    return `<div class="output"><h4>${ICONS.user} ${esc(o.title)} <span class="tag ${o.priority === "High" ? "bad" : "warn"}" style="margin-left:auto">${esc(o.priority)} priority</span></h4>
      <div class="small muted">Assigned to <b style="color:var(--ink)">${esc(o.assignee)}</b></div>
      ${o.questions?.length ? `<ul style="margin:8px 0 0;padding-left:18px;font-size:13px;display:grid;gap:4px">${o.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
      ${prec ? `<div class="check" style="margin-top:8px"><span></span><div class="prec"><b>Precedent:</b> ${esc(prec.reviewer)} approved a similar exception on ${esc(prec.invoice_number)}: “${esc(prec.note)}”</div></div>` : ""}
      ${open ? `<form class="review-form" id="review-form">
        <input name="reviewer" placeholder="Your name" maxlength="80" required>
        <textarea name="note" placeholder="Note for the audit trail (e.g. 'Called vendor on master number - change confirmed')" maxlength="1000"></textarea>
        <div class="row"><button class="btn ok" data-a="approve" type="button">${ICONS.check} Approve for payment</button>
        <button class="btn violet" data-a="return" type="button">${ICONS.mail} Return to vendor</button>
        <button class="btn danger" data-a="reject" type="button">${ICONS.x} Reject</button></div></form>` : ""}
    </div>`;
  }
  return "";
}

function wireReview(form, view) {
  const name = form.elements.reviewer;
  name.value = localStorage.getItem("reviewer") || "";
  $$("[data-a]", form).forEach((b) => (b.onclick = async () => {
    if (!name.value.trim()) { name.focus(); return toast("Enter your name for the audit trail.", true); }
    localStorage.setItem("reviewer", name.value.trim());
    $$("button", form).forEach((x) => (x.disabled = true));
    try {
      view.run = await api(`/api/runs/${view.id}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: b.dataset.a, reviewer: name.value.trim(), note: form.elements.note.value }),
      });
      toast(`Recorded: ${DEC[view.run.final_decision].long}`);
      drawAll(view);
      refreshQueueBadge();
    } catch (e) {
      toast(e.message, true);
      $$("button", form).forEach((x) => (x.disabled = false));
    }
  }));
}

async function replay(view) {
  clearTimers();
  state.runView = view;
  const evs = (await api(`/api/runs/${view.id}/events?after=0`)).events;
  const doneRun = view.run;
  view.done = false;
  view.expanded.clear();
  state.config.stages.forEach((s) => (view.stages[s.key] = { status: "pending", logs: [], checks: [], summary: "", data: null, ms: null }));
  drawAll(view);
  let i = 0;
  const t = setInterval(() => {
    if (state.runView !== view) return clearInterval(t);
    const ev = evs[i++];
    if (!ev) {
      clearInterval(t);
      view.done = true;
      view.run = doneRun;
      drawAll(view);
      return;
    }
    applyEvent(view, ev, true);
    drawTimeline(view);
  }, 70);
  state.timers.push(t);
}

/* ------------------------------------------------------------------ dashboard */
const COLORS = { APPROVED: "#16a34a", NEEDS_REVIEW: "#f59e0b", RETURNED: "#7c3aed", REJECTED: "#dc2626" };

async function renderDashboard() {
  const ui = { decision: "", q: "" };
  app.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><p>Every invoice the process has handled - outcome, who decided, and why. Click a row to open its full audit trail.</p></div>
      <a class="btn primary" href="#/new">${ICONS.upload} Process an invoice</a></div>
    <div id="seed-banner"></div>
    <div class="kpis" id="kpis"></div>
    <div class="charts" id="charts"></div>
    <div class="section-title">Run history</div>
    <div class="card card-pad">
      <div class="toolbar">
        <div class="filters" id="filters">${[["", "All"], ["APPROVED", "Approved"], ["NEEDS_REVIEW", "Needs review"], ["RETURNED", "Returned"], ["REJECTED", "Rejected"]]
          .map(([k, l]) => `<button class="filter ${k === "" ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
        <input class="search" id="search" placeholder="Search vendor, invoice no., PO…">
      </div>
      <div id="runs-table"></div>
    </div>`;
  $$("#filters [data-f]").forEach((b) => (b.onclick = () => {
    ui.decision = b.dataset.f;
    $$("#filters .filter").forEach((x) => x.classList.toggle("on", x === b));
    load();
  }));
  let deb;
  $("#search").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { ui.q = e.target.value.trim(); load(); }, 250); };

  async function load() {
    const params = new URLSearchParams();
    if (ui.decision) params.set("decision", ui.decision);
    if (ui.q) params.set("q", ui.q);
    const [stats, runs] = await Promise.all([api("/api/stats"), api(`/api/runs?${params}`)]);
    drawKpis(stats);
    drawRuns(runs);
  }
  await load();
  state.timers.push(setInterval(() => load().catch(() => {}), 4000));
}

function drawKpis(s) {
  $("#seed-banner").innerHTML = s.seeding ? `<div class="banner">${ICONS.spin.replace("<svg", '<svg style="width:16px;height:16px;animation:spin 1s linear infinite"')} Preparing demo history - re-processing 7 past invoices through the live pipeline…</div>` : "";
  const k = (label, value, hint) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="hint">${hint}</div></div>`;
  $("#kpis").innerHTML =
    k("Invoices processed", s.total_runs, s.in_progress ? `${s.in_progress} running now` : "all outcomes") +
    k("Straight-through", `${s.straight_through_rate}%`, "auto-approved, no human touch") +
    k("Open reviews", s.open_reviews, `${s.human_reviewed} resolved by people`) +
    k("Value approved", inrShort(s.value_approved), `of ${inrShort(s.value_processed)} received`) +
    k("Machine time / invoice", secs(s.avg_compute_ms), "excludes demo pacing");
  const total = Object.values(s.decisions).reduce((a, b) => a + b, 0) || 1;
  let acc = 0;
  const segs = ["APPROVED", "NEEDS_REVIEW", "RETURNED", "REJECTED"].map((d) => {
    const v = s.decisions[d] || 0, from = acc, to = acc + (v / total) * 360;
    acc = to;
    return `${COLORS[d]} ${from}deg ${to}deg`;
  }).join(", ");
  const reasons = s.exceptions_by_reason || [];
  const maxR = Math.max(1, ...reasons.map((r) => r[1]));
  const maxD = Math.max(1, ...s.daily.map((d) => d.APPROVED + d.NEEDS_REVIEW + d.RETURNED + d.REJECTED));
  $("#charts").innerHTML = `
    <div class="card card-pad"><div class="card-head"><h3>Outcomes</h3></div>
      <div class="donut-wrap"><div class="donut" style="background:conic-gradient(${segs})"><span>${s.total_runs}</span></div>
      <div class="legend">${["APPROVED", "NEEDS_REVIEW", "RETURNED", "REJECTED"].map((d) => `<div><i style="background:${COLORS[d]}"></i>${DEC[d].long}<b>${s.decisions[d] || 0}</b></div>`).join("")}</div></div></div>
    <div class="card card-pad"><div class="card-head"><h3>Why invoices didn't go straight through</h3></div>
      <div class="hbars">${reasons.length ? reasons.map(([l, n]) => `<div class="hbar"><span>${esc(l)}</span><div class="track"><i style="width:${(n / maxR) * 100}%"></i></div><b>${n}</b></div>`).join("") : '<div class="muted small">No exceptions yet.</div>'}</div></div>
    <div class="card card-pad"><div class="card-head"><h3>Last 21 days</h3><span class="small muted">by outcome</span></div>
      <div class="vbars">${s.daily.map((d) => {
        const t = d.APPROVED + d.NEEDS_REVIEW + d.RETURNED + d.REJECTED;
        return `<div class="vbar" title="${fmtD(d.date)}: ${t} invoice(s)" style="height:${t ? Math.max(6, (t / maxD) * 100) : 2}%">${["APPROVED", "NEEDS_REVIEW", "RETURNED", "REJECTED"].map((k) => d[k] ? `<i style="height:${(d[k] / t) * 100}%;background:${COLORS[k]}"></i>` : "").join("")}</div>`;
      }).join("")}</div>
      <div class="vlabels">${s.daily.map((d) => `<span>${new Date(d.date + "T00:00:00").getDate()}</span>`).join("")}</div></div>`;
}

function drawRuns(runs) {
  if (!runs.length) { $("#runs-table").innerHTML = `<div class="empty">No runs match.</div>`; return; }
  $("#runs-table").innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>Run</th><th>Received</th><th>Vendor</th><th>Invoice</th><th>PO</th><th class="num">Amount</th><th>Read</th><th>Outcome</th><th>Decided by</th><th class="num">Duration</th></tr>
    ${runs.map((r) => {
      const key = decKey(r);
      const human = r.decided_by && r.decided_by !== "agent";
      const why = key === "APPROVED" && !human ? (r.po_inferred ? "PO inferred from contents" : "")
        : (r.headline || "").split(" · ").slice(1).join(" · ");
      return `<tr class="click" data-href="#/runs/${r.id}">
        <td><b>#${r.id}</b></td><td>${fmtDT(r.created_at)}${r.seeded ? '<div class="sub">history</div>' : ""}</td>
        <td>${esc(r.vendor_name || "—")}<div class="sub">${esc(r.filename)}</div></td>
        <td style="white-space:nowrap">${esc(r.invoice_number || "—")}<div class="sub">${fmtD(r.invoice_date)}</div></td>
        <td>${esc(r.po_number || "—")}${r.po_inferred ? ' <span class="tag warn">inferred</span>' : ""}</td>
        <td class="num">${inr(r.total)}</td>
        <td>${r.read_method === "ocr" ? '<span class="tag brand">OCR</span>' : r.read_method === "text-layer" ? '<span class="tag">Digital</span>' : "—"}${r.ai_used ? ' <span class="tag brand">AI</span>' : ""}</td>
        <td>${chip(key, key === "RUNNING" ? `Processing${r.current_stage ? " · " + r.current_stage : ""}` : undefined)}${why && key !== "RUNNING" ? `<div class="sub" style="max-width:280px">${esc(why)}</div>` : ""}</td>
        <td>${r.decided_by && r.decided_by !== "agent" ? `${ICONS.user.replace("<svg", '<svg style="width:13px;height:13px;vertical-align:-2px"')} ${esc(r.decided_by)}` : r.status === "running" ? "—" : "Agent"}</td>
        <td class="num">${secs(r.duration_ms)}</td></tr>`;
    }).join("")}</table></div>`;
  $$("#runs-table tr[data-href]").forEach((tr) => (tr.onclick = () => (location.hash = tr.dataset.href)));
}

/* ------------------------------------------------------------------ queue */
async function renderQueue() {
  app.innerHTML = `<div class="page-head"><div><h1>Review queue</h1><p>Invoices the agent would not decide alone. Each one names who should look at it and the exact question to answer - nothing is auto-paid while it sits here.</p></div></div><div id="q" class="grid"></div>`;
  const runs = await api("/api/runs?decision=NEEDS_REVIEW");
  if (!runs.length) { $("#q").innerHTML = `<div class="card empty">Nothing waiting for review. 🎉</div>`; return; }
  $("#q").innerHTML = runs.map((r) => {
    const key = decKey(r);
    return `<div class="card card-pad queue-item"><div>
      ${chip(key)} <span class="tag ${r.priority === "High" ? "bad" : "warn"}">${esc(r.priority || "Normal")} priority</span> <span class="tag">${esc(r.queue || "AP team")}</span>
      <h4>${esc(r.headline)}</h4>
      <div class="q">${esc(r.vendor_name || "Unknown vendor")} · ${esc(r.invoice_number || "no invoice no.")} · <b>${inr(r.total)}</b> · received ${fmtDT(r.created_at)}</div></div>
      <a class="btn primary" href="#/runs/${r.id}">Review →</a></div>`;
  }).join("");
}

/* ------------------------------------------------------------------ reference */
async function renderReference(tab) {
  const data = await api("/api/reference");
  const tabs = [["pos", "Purchase orders"], ["vendors", "Vendor master"], ["grns", "Goods receipts"], ["policy", "Policy (rules)"]];
  app.innerHTML = `<div class="page-head"><div><h1>Reference data</h1><p>The "procurement system" every invoice is checked against: ${esc(data.company.name)}'s vendor master, open POs (with live consumption from approved invoices), goods receipts and the AP policy.</p></div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${k === tab ? "on" : ""}" data-t="${k}">${l}</button>`).join("")}</div><div id="ref"></div>`;
  $$("[data-t]").forEach((b) => (b.onclick = () => (location.hash = `#/reference/${b.dataset.t}`)));
  const el = $("#ref");
  if (tab === "vendors") {
    el.innerHTML = `<div class="card"><div class="tbl-wrap" style="border:0"><table><tr><th>ID</th><th>Vendor</th><th>GSTIN</th><th>State</th><th>Status</th><th>Bank (verified)</th><th>Terms</th><th>Master contact</th></tr>
      ${data.vendors.map((v) => `<tr><td>${esc(v.vendor_id)}</td><td><b>${esc(v.name)}</b><div class="sub">${esc(v.category)}</div></td><td class="mono">${esc(v.gstin)}</td><td>${esc(v.state)}</td>
        <td>${v.status === "approved" ? '<span class="tag ok">approved</span>' : `<span class="tag bad">${esc(v.status)}</span><div class="sub" style="max-width:220px">${esc(v.block_reason || "")}</div>`}</td>
        <td>${esc(v.bank.bank_name)} ${esc(v.bank.account)}<div class="sub">${esc(v.bank.ifsc)}</div></td><td>${v.payment_terms_days} days</td><td>${esc(v.email)}<div class="sub">${esc(v.phone)}</div></td></tr>`).join("")}</table></div></div>`;
  } else if (tab === "grns") {
    el.innerHTML = `<div class="card"><div class="tbl-wrap" style="border:0"><table><tr><th>GRN</th><th>PO</th><th>Date</th><th>Received (line: qty)</th></tr>
      ${data.goods_receipts.map((g) => `<tr><td><b>${esc(g.grn_number)}</b></td><td>${esc(g.po_number)}</td><td>${fmtD(g.date)}</td><td>${Object.entries(g.lines).map(([l, q]) => `line ${esc(l)}: ${Number(q).toLocaleString("en-IN")}`).join(" · ")}</td></tr>`).join("")}</table></div></div>`;
  } else if (tab === "policy") {
    const p = data.policy;
    const items = [
      ["Unit price tolerance vs PO", `±${p.price_tolerance_pct}%`, "Per line. Beyond this the buyer must confirm a price change or ask for a credit note."],
      ["Total price excess cap", inr(p.amount_tolerance_abs), "Even if every line is within %, the total excess over PO prices cannot exceed this."],
      ["Cumulative billing vs PO value", `+${p.po_overbilling_tolerance_pct}%`, "Across ALL invoices on a PO - catches split-invoice over-billing."],
      ["Auto-approval limit", inrShort(p.auto_approve_limit), "Clean invoices above this still need Finance Controller sign-off."],
      ["PO inference confidence", `≥ ${p.po_inference_min_score} (margin ≥ ${p.po_inference_min_margin})`, "When no PO is quoted, the best candidate must be clearly better than the next."],
      ["Near-duplicate window", `±${p.fuzzy_duplicate_window_days} days`, "Same vendor + same amount within this window but a different number → human check."],
      ["OCR confidence floor", `${p.ocr_min_confidence}%`, "Below this, a person verifies the figures against the image."],
      ["3-way match for goods", p.goods_require_grn ? "Required" : "Off", "Billed quantity (cumulative) cannot exceed quantity received on GRNs. Services use 2-way match."],
      ["Maximum invoice age", `${p.invoice_max_age_days} days`, "Older invoices are flagged (GST input credit time limits)."],
      ["Rounding tolerance", inr(p.rounding_tolerance), "Allowed difference in arithmetic checks."],
    ];
    el.innerHTML = `<div class="banner">${esc(p.name)} · ${esc(p.version)} - deterministic rules, so every decision is reproducible and explainable.</div>
      <div class="policy-grid">${items.map(([t, v, d]) => `<div class="card card-pad policy-item"><div class="muted small">${t}</div><div class="v">${esc(v)}</div><div class="small muted">${d}</div></div>`).join("")}</div>`;
  } else {
    el.innerHTML = `<div class="po-grid">${data.purchase_orders.map((po) => {
      const pct = po.utilisation_pct;
      return `<div class="card card-pad">
        <div class="card-head"><div><h3>PO ${esc(po.po_number)}</h3><div class="small muted">${esc(po.vendor_name)} · raised ${fmtD(po.po_date)} · ${esc(po.requester)}</div></div>
          <span class="tag ${po.type === "goods" ? "brand" : ""}">${esc(po.type)}</span></div>
        <div class="util"><div class="small" style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Invoiced ${inr(po.billed_value)} of ${inr(po.value_ex_tax)}</span><b class="${pct > 100 ? "bad-text" : ""}">${pct}%</b></div>
          <div class="util-bar"><div class="${pct > 100 ? "over" : "this"}" style="width:${Math.min(pct, 100)}%"></div></div></div>
        <div class="tbl-wrap" style="margin-top:12px"><table><tr><th>#</th><th>Item</th><th class="num">Price</th><th class="num">Ordered</th><th class="num">Received</th><th class="num">Invoiced</th></tr>
          ${po.lines.map((l) => `<tr><td>${l.line_no}</td><td>${esc(l.description)}</td><td class="num">${inr(l.unit_price)}</td><td class="num">${Number(l.qty).toLocaleString("en-IN")} ${esc(l.uom)}</td><td class="num">${l.received_qty == null ? '<span class="faint">n/a</span>' : Number(l.received_qty).toLocaleString("en-IN")}</td><td class="num">${Number(l.billed_qty).toLocaleString("en-IN")}</td></tr>`).join("")}
        </table></div>
        ${po.invoices.length ? `<div class="small muted" style="margin-top:8px">Invoices posted: ${po.invoices.map((i) => `<a href="#/runs/${i.run_id}">${esc(i.invoice_number)}</a> (${inr(i.amount_ex_tax)})`).join(", ")}</div>` : ""}
        ${po.note ? `<div class="small faint" style="margin-top:6px">${esc(po.note)}</div>` : ""}</div>`;
    }).join("")}</div>`;
  }
}

/* ------------------------------------------------------------------ about */
function renderAbout() {
  const c = state.config;
  const stagesHelp = {
    intake: "Accepts the PDF and fingerprints it (SHA-256) so the exact same file can never be paid twice.",
    read: "Digital PDFs are read exactly. Scans are rendered at 300 DPI, straightened and OCR'd, with a confidence score.",
    extract: "A rule-based reader finds labelled fields and table columns. An optional AI reader reads independently; disagreements are settled by evidence.",
    validate: "Lines × rates, subtotal, GST maths and type (CGST/SGST vs IGST), total in words, our GSTIN, dates.",
    vendor: "Vendor master lookup by GSTIN; blocked vendors stopped; bank details on the invoice compared with verified master details.",
    duplicate: "Same file, same normalised invoice number (TS/26-27/0418 = TS-2627-418), or same amount + date.",
    po_match: "Uses the quoted PO, or infers it from vendor, items, prices and remaining PO value - with a score.",
    reconcile: "Prices vs PO, cumulative quantity and value across all earlier invoices, and goods received (3-way match).",
    decide: "Deterministic policy picks the outcome and produces the output: payment instruction, review task or vendor email.",
  };
  app.innerHTML = `
    <div class="page-head"><div><h1>How it works</h1><p>An AP clerk's invoice routine - open, read, check the vendor, find the PO, compare, decide - as a process that runs end-to-end, explains every step, and asks a person only when the evidence doesn't support a safe decision.</p></div></div>
    <div class="flow">${c.stages.map((s, i) => `<div class="st ${s.key === "extract" ? "ai" : ""}"><div class="n">STAGE ${i + 1}</div><h4>${esc(s.name)}</h4><p>${esc(stagesHelp[s.key] || s.desc)}</p></div>`).join("")}</div>
    <div class="outcomes">
      <div class="outcome"><span class="chip APPROVED">Approved</span><h4>Pay it</h4><p>Everything verified within tolerance. Posted to the ledger and scheduled - always to the vendor-master bank account.</p></div>
      <div class="outcome"><span class="chip NEEDS_REVIEW">Needs review</span><h4>Ask the right person</h4><p>Routed to the buyer, stores, procurement or the AP controller with the specific question to answer.</p></div>
      <div class="outcome"><span class="chip RETURNED">Returned</span><h4>Vendor must fix it</h4><p>Missing number, wrong GST, bad maths. A correction email is drafted to the master contact.</p></div>
      <div class="outcome"><span class="chip REJECTED">Rejected</span><h4>Never pay it</h4><p>Duplicates and blocked vendors. Processing stops; a courtesy note is drafted.</p></div>
    </div>
    <div class="section-title">Design choices</div>
    <div class="principles">
      <div class="card card-pad principle"><h4>AI reads, rules decide</h4><p>Reading messy documents is a perception problem - that's where OCR and an LLM help. Deciding whether to pay is a policy problem - that stays deterministic, so the same invoice always gets the same answer and every decision can be audited.</p></div>
      <div class="card card-pad principle"><h4>Maker-checker extraction</h4><p>Two independent readers. Agreement raises confidence; disagreement is settled by hard evidence on the page - the GSTIN checksum, line maths, the total written in words. If evidence can't settle a money or identity field, a person checks it.</p></div>
      <div class="card card-pad principle"><h4>Judge the invoice in context</h4><p>Most expensive mistakes look fine in isolation: the third invoice on a fully-billed PO, a re-sent copy with a reformatted number, a perfect invoice with new bank details. The process checks history and master data, not just the PDF.</p></div>
      <div class="card card-pad principle"><h4>Escalate with a question, not a shrug</h4><p>"Needs review" always says who should look and what to decide, shows the evidence and any precedent (how a reviewer handled the same exception before), and records the human decision on the audit trail.</p></div>
    </div>
    <div class="section-title">Edge cases built in</div>
    <div class="card"><div class="tbl-wrap" style="border:0"><table>
      <tr><th>Scenario</th><th>Why it's hard</th><th>What the process does</th></tr>
      <tr><td><b>Scanned invoice, no PO number</b></td><td>No text layer; OCR misreads the GSTIN; vendor has two open POs.</td><td>OCR + deskew, repairs the GSTIN via its checksum, infers the PO with a score and margin, approves - and says it inferred.</td></tr>
      <tr><td><b>Split deliveries, over-billed PO</b></td><td>The invoice is valid on its own; the problem is the two invoices approved before it.</td><td>Tracks cumulative quantity, value and goods receipts per PO line; routes to the buyer at 115%.</td></tr>
      <tr><td><b>Duplicate in disguise</b></td><td>New file, new layout, "TS-2627-418" vs "TS/26-27/0418".</td><td>Normalises invoice numbers; rejects with a link to the original run and drafts a polite note.</td></tr>
      <tr><td><b>Perfect invoice, new bank account</b></td><td>Every number is right - it's the payment destination that's wrong.</td><td>Compares bank details with the verified master, holds payment, asks for a call-back on the master phone number.</td></tr>
      <tr><td>Price above PO · Missing invoice number · Blocked vendor</td><td>Commercial, compliance and master-data problems.</td><td>Buyer review with precedent · return with drafted email · reject.</td></tr>
    </table></div></div>
    <div class="section-title">Assumptions</div>
    <div class="card card-pad small" style="display:grid;gap:6px;color:var(--ink-2)">
      <div>• Buyer is a fictional Indian mid-size retailer (Nimbus Retail, Bengaluru); invoices are GST tax invoices in INR; dates are day-first.</div>
      <div>• Vendor master, POs and goods receipts are JSON stand-ins for an ERP; approved invoices consume PO balances in a ledger.</div>
      <div>• Policy thresholds (tolerances, limits) are configurable data, not code. The demo dataset and invoices are synthetic.</div>
      <div>• Payment always goes to the verified master bank account; vendor emails go to the master contact, never to contact details printed on the invoice.</div>
    </div>`;
}

boot();
