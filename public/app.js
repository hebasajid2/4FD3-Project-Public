"use strict";
/* Smart Nursing DSS front end. Talks only to /api/* (the routing system). No build step. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Colour-blind-safe palette (Okabe-Ito). Colour is never the only cue: text labels are always shown.
const PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"];
const ACUITY_FILL = { 0: "#FFFFFF", 1: "#E8F1FA", 2: "#BBD7EE", 3: "#F7D58B", 4: "#EE8A55", 5: "#B2182B" };

function textOn(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return (1.05) / (L + 0.05) > (L + 0.05) / 0.05 ? "#FFFFFF" : "#111111";
}

const S = { unitId: null, snap: null, draft: {}, eval: null, unassigned: [], source: "saved" };

// ---------------------------------------------------------------- helpers
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch { data = { error: "The server sent an unreadable response." }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function say(msg, kind = "") { const el = $("#status"); el.textContent = msg; el.className = "status " + kind; }
async function guard(fn) { try { await fn(); } catch (e) { say(e.message, "err"); } }
const nurseById = (id) => S.snap.nurses.find((n) => n.nurse_id === Number(id));
const nurseColor = (id) => { const i = S.snap.nurses.findIndex((n) => n.nurse_id === Number(id)); return PALETTE[(i < 0 ? 7 : i) % PALETTE.length]; };
const allBeds = () => S.snap.rooms.flatMap((r) => r.beds.map((b) => ({ ...b, room: r })));

function table(cols, rows, caption) {
  if (!rows.length) return `<p class="hint">Nothing to show yet.</p>`;
  const head = cols.map((c) => `<th scope="col"${c.num ? ' class="num"' : ""}>${esc(c.h)}</th>`).join("");
  const body = rows.map((r) => `<tr>${cols.map((c) => {
    const v = typeof c.v === "function" ? c.v(r) : esc(r[c.v]);
    return `<td${c.num ? ' class="num"' : ""}>${v}</td>`;
  }).join("")}</tr>`).join("");
  return `<div class="scroll"><table><caption class="sr">${esc(caption || "")}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ---------------------------------------------------------------- loading
async function load(resetDraft = true) {
  const snap = await api("/api/snapshot" + (S.unitId ? `?unit_id=${S.unitId}` : ""));
  S.snap = snap;
  const sel = $("#unit-select");
  sel.innerHTML = snap.units.map((u) => `<option value="${u.unit_id}">${esc(u.unit_name)}</option>`).join("");
  if (!snap.unit) {
    say("No units yet. Open Setup and load the demo unit or add your own.", "err");
    showTab("setup");
    return;
  }
  S.unitId = snap.unit.unit_id;
  sel.value = S.unitId;
  if (resetDraft) {
    S.draft = { ...snap.plan };
    S.eval = { violations: snap.violations, metrics: snap.metrics, comparison: snap.comparison };
    S.unassigned = [];
    S.source = "saved";
  }
  renderAll();
}

function renderAll() {
  renderAssign(); renderDashboard(); renderBellPicker(); renderSetup();
}

// ---------------------------------------------------------------- shared renderers
function barsHTML(metrics) {
  return metrics.per_nurse.map((p) => {
    const pct = Math.round(p.utilization_pct);
    const over = p.load > p.capacity;
    return `<div class="bar-row${over ? " over" : ""}">
      <span>${esc(p.name)} <span class="pill">${esc(p.qualification)}</span></span>
      <div class="bar-track" role="img" aria-label="${esc(p.name)}: ${p.load} of ${p.capacity} acuity points, ${pct} percent${over ? ", OVER CAPACITY" : ""}">
        <div class="bar-fill" style="width:${Math.min(100, pct)}%;background:${nurseColor(p.nurse_id)}"></div></div>
      <span>${p.load}/${p.capacity} (${pct}%)${over ? " OVER" : ""}</span></div>`;
  }).join("");
}

function comparisonHTML(rows, label) {
  return table([
    { h: "Metric (lower is better)", v: "metric" },
    { h: "Manual baseline", v: "baseline", num: true },
    { h: label, v: "candidate", num: true },
    { h: "Change", v: "change", num: true },
  ], rows, "Comparison with manual baseline");
}

function mapHTML(plan, metrics, { colorBy = "nurse", routes = true } = {}) {
  const rooms = S.snap.rooms, [sx, sy] = S.snap.station;
  const scale = 14, boxW = 124, boxH = 84, pad = 40;
  const xs = [...rooms.map((r) => r.x), sx], ys = [...rooms.map((r) => r.y), sy];
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const px = (x) => pad + boxW / 2 + (x - minX) * scale;
  const py = (y) => pad + boxH / 2 + (maxY - y) * scale;
  const W = px(maxX) + boxW / 2 + pad, H = py(minY) + boxH / 2 + pad;

  let body = "";
  const used = new Set();
  for (const r of rooms) {
    const owners = [...new Set(r.beds.map((b) => plan[b.bed_id]).filter((v) => v !== undefined))];
    const unassigned = r.beds.some((b) => plan[b.bed_id] === undefined);
    const topAcuity = Math.max(0, ...r.beds.map((b) => b.acuity || 0));
    let fill = "#FFFFFF", state;
    if (colorBy === "acuity") { fill = ACUITY_FILL[topAcuity]; state = `highest acuity ${topAcuity}`; }
    if (owners.length === 1 && !unassigned) { if (colorBy === "nurse") fill = nurseColor(owners[0]); used.add(owners[0]); }
    const nurseText = owners.length === 0 ? "Unassigned" : owners.length > 1 ? "Split: " + owners.map((o) => nurseById(o)?.name.split(" ")[0]).join("/") :
      nurseById(owners[0])?.name + (unassigned ? " (partial)" : "");
    const split = owners.length > 1 || (owners.length === 1 && unassigned);
    if (colorBy === "nurse" && (owners.length === 0 || split)) fill = "#FFFFFF";
    const fg = textOn(fill), x = px(r.x) - boxW / 2, y = py(r.y) - boxH / 2;
    const bedsText = r.beds.map((b) => `${b.bed_number}:${b.acuity ?? "–"}${(b.acuity || 0) >= 4 ? "!" : ""}`).join("  ");
    const label = `Room ${r.room_number}. ${nurseText}. Beds: ${r.beds.map((b) => `${b.bed_number} acuity ${b.acuity ?? "empty"}`).join(", ")}.`;
    body += `<g role="listitem" aria-label="${esc(label)}"><title>${esc(label)}</title>
      <rect x="${x}" y="${y}" width="${boxW}" height="${boxH}" rx="8" fill="${fill}" stroke="#111" stroke-width="${topAcuity >= 4 ? 5 : 2}" ${owners.length === 0 || split ? 'stroke-dasharray="7 4"' : ""}/>
      <text x="${x + 8}" y="${y + 22}" font-size="15" font-weight="700" fill="${fg}">Room ${esc(r.room_number)}</text>
      <text x="${x + 8}" y="${y + 43}" font-size="12" fill="${fg}">${esc(bedsText)}</text>
      <text x="${x + 8}" y="${y + 63}" font-size="12" fill="${fg}">${esc(nurseText.length > 17 ? nurseText.slice(0, 16) + "…" : nurseText)}</text></g>`;
  }

  let routeSVG = "", badgeSVG = "";
  if (routes && metrics) {
    for (const p of metrics.per_nurse) {
      if (!p.room_ids.length) continue;
      const pts = [[sx, sy], ...p.room_ids.map((id) => { const r = rooms.find((q) => q.room_id === id); return [r.x, r.y]; })];
      const d = pts.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`).join(" ");
      routeSVG += `<polyline points="${d}" fill="none" stroke="#fff" stroke-width="7" opacity=".9"/>
        <polyline points="${d}" fill="none" stroke="#111" stroke-width="3" stroke-dasharray="8 5"/>`;
      pts.slice(1).forEach(([x, y], i) => {
        badgeSVG += `<circle cx="${px(x) + boxW / 2 - 14}" cy="${py(y) - boxH / 2 + 14}" r="11" fill="#fff" stroke="#111" stroke-width="2"/>
          <text x="${px(x) + boxW / 2 - 14}" y="${py(y) - boxH / 2 + 19}" font-size="13" font-weight="700" text-anchor="middle" fill="#111">${i + 1}</text>`;
      });
    }
  }
  const station = `<g><polygon points="${px(sx)},${py(sy) - 16} ${px(sx) + 16},${py(sy)} ${px(sx)},${py(sy) + 16} ${px(sx) - 16},${py(sy)}" fill="#111"/>
    <text x="${px(sx)}" y="${py(sy) + 32}" font-size="12" font-weight="700" text-anchor="middle" fill="currentColor">Station</text></g>`;

  const summary = `Floor map of ${S.snap.unit.unit_name}: ${rooms.length} rooms. A table with the same information is under Review and edit.`;
  const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(summary)}"><title>${esc(summary)}</title>
    ${routeSVG}<g role="list" aria-label="Rooms">${body}</g>${badgeSVG}${station}</svg>`;

  let legend;
  if (colorBy === "nurse") {
    legend = S.snap.nurses.filter((n) => used.has(n.nurse_id)).map((n) => `<li><span class="swatch" style="background:${nurseColor(n.nurse_id)}"></span>${esc(n.name)}</li>`).join("") +
      `<li><span class="swatch" style="background:#fff;border-style:dashed"></span>Unassigned / split room</li>`;
  } else {
    legend = [1, 2, 3, 4, 5].map((a) => `<li><span class="swatch" style="background:${ACUITY_FILL[a]}"></span>Acuity ${a}</li>`).join("");
  }
  return `${svg}<ul class="legend" aria-label="Colour key">${legend}
    <li>Bed label:acuity (<b>!</b> = acuity 4–5, thick border)</li><li>◇ Station · numbered circles = walking order</li></ul>`;
}

// ---------------------------------------------------------------- assignments tab
function renderAssign() {
  if (!S.snap?.unit) return;
  const nurses = S.snap.nurses;
  const opts = (selected, blank) => `<option value="">${blank}</option>` + nurses.map((n) =>
    `<option value="${n.nurse_id}"${String(selected) === String(n.nurse_id) ? " selected" : ""}>${esc(n.name)} [${esc(n.qualification)}]${n.available ? "" : " (unavailable)"}</option>`).join("");

  let rows = "";
  for (const r of S.snap.rooms) {
    rows += `<tr class="room-start"><th scope="rowgroup" colspan="3">Room ${esc(r.room_number)}</th>
      <td><label class="sr" for="room-${r.room_id}">Set whole room ${esc(r.room_number)}</label>
      <select id="room-${r.room_id}" data-room="${r.room_id}">${opts("", "Set whole room…")}</select></td></tr>`;
    for (const b of r.beds) {
      rows += `<tr><td>${esc(r.room_number)}</td><td>${esc(b.bed_number)}</td>
        <td>${b.acuity ?? "–"}${(b.acuity || 0) >= 4 ? " <b>!</b>" : ""}</td>
        <td><label class="sr" for="bed-${b.bed_id}">Nurse for room ${esc(r.room_number)} bed ${esc(b.bed_number)}</label>
        <select id="bed-${b.bed_id}" data-bed="${b.bed_id}">${opts(S.draft[b.bed_id] ?? "", "— unassigned —")}</select></td></tr>`;
    }
  }
  $("#draft-table tbody").innerHTML = rows;
  renderAssignEval();
}

function renderAssignEval() {
  const ev = S.eval;
  const items = ev.violations.map((v) => `<li class="${v.severity}"><strong>${v.severity === "ERROR" ? "Error" : "Warning"}:</strong> ${esc(v.message)}</li>`);
  S.unassigned.forEach((u) => items.push(`<li class="ERROR"><strong>Error:</strong> The optimizer could not place room ${esc(S.snap.rooms.find((r) => r.room_id === u.room_id)?.room_number)}. ${esc(u.reason)}</li>`));
  $("#violations").innerHTML = items.length ? items.join("") : `<li class="OK"><strong>No rule violations.</strong> Capacity, qualification, availability and mobility checks all pass.</li>`;
  $("#bars-assign").innerHTML = barsHTML(ev.metrics);
  $("#cmp-assign").innerHTML = comparisonHTML(ev.comparison, "This plan");
  $("#map-assign").innerHTML = mapHTML(S.draft, ev.metrics, { colorBy: $("#map-color").value, routes: $("#map-routes").checked });
}

async function reevaluate() {
  const ev = await api("/api/assignments/evaluate", { unit_id: S.unitId, plan: S.draft });
  S.eval = ev; S.unassigned = []; renderAssignEval();
}

$("#draft-table").addEventListener("change", (e) => guard(async () => {
  const t = e.target;
  if (t.dataset.bed !== undefined) {
    if (t.value === "") delete S.draft[t.dataset.bed]; else S.draft[t.dataset.bed] = Number(t.value);
    await reevaluate();
  } else if (t.dataset.room !== undefined && t.value !== "") {
    const room = S.snap.rooms.find((r) => r.room_id === Number(t.dataset.room));
    room.beds.forEach((b) => { S.draft[b.bed_id] = Number(t.value); });
    const id = t.id;
    await reevaluate(); renderAssign(); $("#" + id)?.focus();
  }
}));
["#map-color", "#map-routes"].forEach((s) => $(s).addEventListener("change", () => S.eval && renderAssignEval()));

$("#btn-opt").onclick = () => guard(async () => {
  say("Optimizing…");
  const rec = await api("/api/assignments/recommend", { unit_id: S.unitId, prefer_continuity: $("#chk-continuity").checked });
  S.draft = rec.plan; S.eval = rec; S.unassigned = rec.unassigned_rooms; S.source = "optimized";
  renderAssign();
  say(`Optimized recommendation ready in ${rec.elapsed_ms} ms. Review it, edit if needed, then save.`, "ok");
});
$("#btn-base").onclick = () => guard(async () => {
  const b = await api("/api/assignments/baseline", { unit_id: S.unitId });
  S.draft = b.plan; S.eval = b; S.unassigned = []; S.source = "baseline"; renderAssign();
  say("Showing the manual baseline (equal room blocks in corridor order, ignoring acuity).", "ok");
});
$("#btn-saved").onclick = () => guard(async () => { await load(true); say("Loaded the saved assignment.", "ok"); });
$("#btn-save").onclick = () => guard(async () => {
  const out = await api("/api/assignments/commit", { unit_id: S.unitId, plan: S.draft, override: $("#chk-override").checked });
  if (!out.saved) { say(out.message, "err"); return; }
  await load(true); say(out.message, "ok");
});

// ---------------------------------------------------------------- dashboard tab
function renderDashboard() {
  if (!S.snap?.unit) return;
  const m = S.snap.metrics, s = m.summary, plan = S.snap.plan;
  const kpi = (v, l) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`;
  $("#kpis").innerHTML = kpi(s.nurses_on_duty, "nurses on duty") + kpi(s.total_load, "total acuity points") +
    kpi(s.workload_std.toFixed(2), "workload std-dev") + kpi(Math.round(s.max_utilization_pct) + "%", "max nurse utilization") +
    kpi(Math.round(s.total_travel_m) + " m", "estimated walking") + kpi(s.rule_errors, "rule errors");
  $("#map-dash").innerHTML = mapHTML(plan, m, { colorBy: "nurse", routes: true });
  $("#bars-dash").innerHTML = barsHTML(m);
  $("#cmp-dash").innerHTML = comparisonHTML(S.snap.comparison, "Saved assignment");
  $("#cards").innerHTML = m.per_nurse.map((p) => {
    const n = nurseById(p.nurse_id);
    const rooms = p.room_ids.map((id) => { const r = S.snap.rooms.find((q) => q.room_id === id);
      return `<li>Room ${esc(r.room_number)} — ${r.beds.map((b) => `${esc(b.bed_number)}:${b.acuity ?? "–"}`).join(", ")}</li>`; }).join("");
    return `<article class="card" style="border-left:10px solid ${nurseColor(p.nurse_id)}"><h3>${esc(p.name)} <span class="pill">${esc(p.qualification)}</span></h3>
      <div>${esc(n.contact || "no device linked")} · ${p.load}/${p.capacity} acuity · ${Math.round(p.travel_m)} m walk${n.mobility_restricted ? " · mobility restricted" : ""}</div>
      <ol>${rooms || "<li>No rooms assigned</li>"}</ol></article>`;
  }).join("");
}

// ---------------------------------------------------------------- call bells tab
function renderBellPicker() {
  if (!S.snap?.unit) return;
  const sel = $("#bell-select"), keep = sel.value;
  sel.innerHTML = allBeds().filter((b) => b.bell_id).map((b) =>
    `<option value="${b.bell_id}">Bell ${b.bell_id} — Room ${esc(b.room.room_number)} Bed ${esc(b.bed_number)}</option>`).join("");
  if (keep) sel.value = keep;
}

function resultHTML(r) {
  const cls = !r.ok ? "bad" : r.flags.length ? "warn" : "good";
  const head = r.ok ? `Delivered to <strong>${esc(r.nurse_name)}</strong> (${esc(r.channel)})` : `<strong>NOT DELIVERED</strong> — ${esc(r.status)}`;
  const where = r.room_number ? `Room ${esc(r.room_number)} Bed ${esc(r.bed_number)}` : "";
  return `<div class="result ${cls}"><div>${head}</div>
    <div>${where}${r.assignment_level ? ` · matched by ${esc(r.assignment_level)}-level assignment` : ""}${r.alert_id ? ` · alert #${r.alert_id}` : ""} · routing ${r.routing_latency_ms.toFixed(1)} ms</div>
    ${r.flags.length ? `<ul>${r.flags.map((f) => `<li>⚠ ${esc(f)}</li>`).join("")}</ul>` : ""}</div>`;
}

async function refreshAlerts() {
  const a = await api("/api/alerts");
  $("#alerts-table").innerHTML = table([
    { h: "#", v: "alert_id" }, { h: "Room / bed", v: (r) => `${esc(r.room_number)} / ${esc(r.bed_number)}` },
    { h: "Nurse", v: "nurse" }, { h: "Status", v: "status" },
    { h: "Action", v: (r) => r.acknowledged_at || !["DISPATCHED", "ESCALATED_UNASSIGNED"].includes(r.status) ? (r.acknowledged_at ? "Acknowledged" : "") :
      `<button data-ack="${r.alert_id}">Acknowledge</button>` },
  ], a.alerts, "Recent alerts");
  $("#inbox-table").innerHTML = table([
    { h: "Nurse", v: (r) => esc(r.nurse || r.channel) }, { h: "Device", v: "channel" },
    { h: "Message", v: (r) => `Room ${esc(r.room_number)} - Bed ${esc(r.bed_number)}` }, { h: "Time (UTC)", v: (r) => esc(r.sent_at?.slice(11, 19)) },
  ], a.inbox, "Messages delivered to nurse devices");
  $("#notif-table").innerHTML = table([
    { h: "#", v: "notification_id" }, { h: "Alert", v: "alert_id" }, { h: "Channel", v: "channel" },
    { h: "Result", v: "delivery_status" }, { h: "Latency (ms)", v: "latency_ms", num: true }, { h: "Detail", v: "detail" },
  ], a.notifications, "Notification log");
}
$("#btn-press").onclick = () => guard(async () => {
  const r = await api("/api/bell", { bell_id: Number($("#bell-select").value) });
  $("#bell-result").innerHTML = resultHTML(r); await refreshAlerts();
  say(r.ok ? "Call bell routed." : "Call bell could NOT be delivered. See the red box.", r.ok ? "ok" : "err");
});
$("#btn-escalate").onclick = () => guard(async () => {
  const out = await api("/api/alerts/escalate", { elapsed_seconds: Number($("#elapsed").value) || 0 });
  $("#bell-result").innerHTML = out.results.length ? out.results.map(resultHTML).join("") : `<div class="result">No alerts are waiting past the acknowledgement timeout.</div>`;
  await refreshAlerts();
});
$("#btn-refresh-alerts").onclick = () => guard(refreshAlerts);
$("#alerts-table").addEventListener("click", (e) => guard(async () => {
  const id = e.target.dataset.ack; if (!id) return;
  const out = await api("/api/alerts/ack", { alert_id: Number(id) });
  say(`Acknowledged after ${out.response_seconds} s.`, "ok"); await refreshAlerts();
}));

// ---------------------------------------------------------------- setup tab
function renderSetup() {
  if (!S.snap?.unit) return;
  const beds = allBeds();
  const fills = {
    rooms: S.snap.rooms.map((r) => `<option value="${r.room_id}">Room ${esc(r.room_number)}</option>`).join(""),
    beds: beds.map((b) => `<option value="${b.bed_id}">Room ${esc(b.room.room_number)} Bed ${esc(b.bed_number)}${b.acuity ? ` (acuity ${b.acuity})` : ""}</option>`).join(""),
    nurses: S.snap.nurses.map((n) => `<option value="${n.nurse_id}">${esc(n.name)}</option>`).join(""),
  };
  $$("select[data-fill]").forEach((s) => { const keep = s.value; s.innerHTML = fills[s.dataset.fill]; if (keep) s.value = keep; });
  fillNurseForm();
  $("#layout-table").innerHTML = table([
    { h: "Room", v: (r) => esc(r.room.room_number) }, { h: "X", v: (r) => r.room.x, num: true }, { h: "Y", v: (r) => r.room.y, num: true },
    { h: "Bed", v: "bed_number" }, { h: "Call bell", v: (r) => r.bell_id ?? "–" }, { h: "Acuity", v: (r) => r.acuity ?? "–" },
  ], beds, "Unit layout");
  $("#nurse-table").innerHTML = table([
    { h: "ID", v: "nurse_id" }, { h: "Name", v: "name" }, { h: "Role", v: "qualification" },
    { h: "Max acuity", v: "max_workload", num: true }, { h: "Device", v: (n) => esc(n.contact || "— none —") },
    { h: "Available", v: (n) => (n.available ? "yes" : "no") }, { h: "Mobility restricted", v: (n) => (n.mobility_restricted ? "yes" : "no") },
  ], S.snap.nurses, "Nurses");
}
function fillNurseForm() {
  const f = $("#f-nurse-edit"), n = nurseById(f.nurse_id.value || S.snap.nurses[0]?.nurse_id);
  if (!n) return;
  f.nurse_id.value = n.nurse_id; f.contact.value = n.contact || "";
  f.available.checked = n.available; f.mobility_restricted.checked = n.mobility_restricted;
}
$("#f-nurse-edit").nurse_id.addEventListener("change", fillNurseForm);

function bindForm(id, path, build, done) {
  $(id).addEventListener("submit", (e) => { e.preventDefault(); guard(async () => {
    const fd = Object.fromEntries(new FormData(e.target).entries());
    const out = await api(path, build(fd, e.target));
    await load(false); say(done, "ok"); if (id === "#f-unit" && out.unit_id) { S.unitId = out.unit_id; await load(true); }
  }); });
}
bindForm("#f-unit", "/api/setup/unit", (d) => ({ name: d.name }), "Unit added.");
bindForm("#f-room", "/api/setup/room", (d) => ({ ...d, unit_id: S.unitId }), "Room added.");
bindForm("#f-bed", "/api/setup/bed", (d) => d, "Bed added.");
bindForm("#f-bell", "/api/setup/bell", (d) => d, "Call bell linked.");
bindForm("#f-patient", "/api/setup/patient", (d) => d, "Patient saved.");
bindForm("#f-nurse", "/api/setup/nurse", (d) => d, "Nurse added.");
bindForm("#f-nurse-edit", "/api/setup/nurse/update", (d, f) => ({ ...d, available: f.available.checked, mobility_restricted: f.mobility_restricted.checked }), "Nurse updated.");
$("#btn-discharge").onclick = () => guard(async () => {
  await api("/api/setup/discharge", { bed_id: Number($("#f-patient").bed_id.value) }); await load(false); say("Bed discharged.", "ok");
});
$("#btn-demo").onclick = () => guard(async () => { const o = await api("/api/setup/demo", {}); S.unitId = o.unit_id; await load(true); say("Demo unit ready.", "ok"); });

async function refreshHistory() {
  const h = await api("/api/assignments/history");
  $("#history-table").innerHTML = table([
    { h: "#", v: "assignment_id" }, { h: "Nurse", v: "nurse_name" }, { h: "Room", v: "room_number" }, { h: "Bed", v: (r) => esc(r.bed_number ?? "(whole room)") },
    { h: "Level", v: "level" }, { h: "Start (UTC)", v: (r) => esc(r.start_time?.slice(0, 19)) }, { h: "End (UTC)", v: (r) => esc(r.end_time?.slice(0, 19) ?? "active") },
  ], h.assignments.slice(0, 60), "Assignment history");
}

// ---------------------------------------------------------------- tabs
const TABS = ["assign", "dash", "bells", "setup"];
function showTab(name) {
  TABS.forEach((t) => {
    const on = t === name;
    $("#tab-" + t).setAttribute("aria-selected", on); $("#tab-" + t).tabIndex = on ? 0 : -1;
    $("#panel-" + t).hidden = !on;
  });
  if (name === "bells") guard(refreshAlerts);
  if (name === "setup") guard(refreshHistory);
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) showTab(b.id.replace("tab-", "")); });
$("#tabs").addEventListener("keydown", (e) => {
  const i = TABS.findIndex((t) => $("#tab-" + t).getAttribute("aria-selected") === "true");
  const next = e.key === "ArrowRight" ? (i + 1) % 4 : e.key === "ArrowLeft" ? (i + 3) % 4 : -1;
  if (next >= 0) { showTab(TABS[next]); $("#tab-" + TABS[next]).focus(); }
});
$("#unit-select").addEventListener("change", (e) => guard(async () => { S.unitId = Number(e.target.value); await load(true); }));

guard(() => load(true));
