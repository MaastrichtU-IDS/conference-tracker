/* Conference Tracker — frontend.
   State comes from /api/bootstrap; everything mutating goes back through the API. */
"use strict";

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const STATUSES = ["Idea","Drafting","Submitted","Under review","Accepted","Rejected","Parked"];
const PROJECTS = ["SULO","AIDAVA","GLIOTWIN","RustDL / OWL reasoner","Minimal ontology","Coffee & metabolism","Other"];

let CONFS = [], MSR = [], SUBS = [], MAP = null, ME = null;
let STARS = { conference: new Set(), milestone: new Set() };
let CONF_BY_ID = new Map();

/* today = the current calendar day in Hawaii (UTC-10), so a deadline counts as
   future until 23:59:59 Hawaii time on its own date — the AoE convention. */
function hawaiiToday() {
  const now = new Date();
  const h = new Date(now.getTime() + now.getTimezoneOffset() * 60000 - 10 * 3600 * 1000);
  return new Date(h.getFullYear(), h.getMonth(), h.getDate());
}
const today = hawaiiToday();
const pd = s => { if (!s) return null; const p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); };
const fmt = s => { const d = pd(s); return d ? `${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}` : "—"; };
const fmtShort = s => { const d = pd(s); return d ? `${d.getDate()} ${MON[d.getMonth()]}` : "—"; };
const days = s => { const d = pd(s); return d ? Math.round((d - today) / 864e5) : null; };
function rel(s) { const n = days(s); if (n === null) return ""; if (n === 0) return "today";
  if (n < 0) return Math.abs(n) + "d ago"; if (n < 31) return "in " + n + "d"; return "in " + Math.round(n / 30.4) + "mo"; }
function urgency(s) { const n = days(s); if (n === null) return "tbd"; if (n < 0) return "closed";
  if (n <= 21) return "urgent"; if (n <= 60) return "soon"; return "open"; }

/* ---------- API ---------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401) { showSignin(); throw new Error("unauthenticated"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON error */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}
let flashTimer = null;
function flash(msg) {
  const el = $("#flash");
  el.textContent = msg; el.classList.add("on");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => el.classList.remove("on"), 2600);
}
function showSignin() { $("#root").hidden = true; $("#signin").classList.remove("hidden"); }

/* ---------- milestone kinds ---------- */
const KINDS = [
  ["abstract","Abstract"],["paper","Full paper"],["proposal","Proposal"],
  ["notification","Notification"],["review","Rebuttal / review"],
  ["final","Camera-ready / final"],["admin","Registration & admin"],["event","Event"],
];
const KINDLABEL = Object.fromEntries(KINDS);
function kindOf(m) {
  const t = `${m.milestone || ""} ${m.track || ""}`;
  if (m.time_basis === "Event local dates" || m.track === "Event") return "event";
  if (/\bopens?\b/i.test(t)) return "admin";
  if (/abstract/i.test(t)) return "abstract";
  if (/full paper|paper submission|poster submission|revised paper|supplementary|^submission\b/i.test(m.milestone || "")) return "paper";
  if (/notification|notices sent|reviews released|decisions/i.test(t)) return "notification";
  if (/camera-ready|materials due|website due|revised version|draft presentation|online materials/i.test(t)) return "final";
  if (/proposal/i.test(t)) return "proposal";
  if (/rebuttal|feedback|discussion|meta-review|author response/i.test(t)) return "review";
  return "admin";
}
const LIFECYCLE = new Set(["abstract","paper","notification","review","final"]);

/* ---------- global filters ---------- */
const F = { q: "", time: "future", starred: false, venues: new Set(), kinds: new Set(["abstract","paper","proposal"]) };
const venueOK = v => F.venues.size === 0 || F.venues.has(v);
function timeOK(dateStr) { const n = days(dateStr); if (n === null) return F.time === "future"; return F.time === "future" ? n >= 0 : n < 0; }
const qMatch = txt => !F.q || txt.toLowerCase().includes(F.q);
const isStar = (kind, id) => STARS[kind].has(id);
const starOK = (confId, msId) => !F.starred || isStar("conference", confId) || (msId && isStar("milestone", msId));

/* ---------- stars ---------- */
async function toggleStar(kind, id) {
  const on = isStar(kind, id);
  on ? STARS[kind].delete(id) : STARS[kind].add(id);   // optimistic
  renderAll();
  try {
    if (on) await api(`/api/stars?kind=${encodeURIComponent(kind)}&target_id=${encodeURIComponent(id)}`, { method: "DELETE" });
    else await api("/api/stars", { method: "POST", body: { kind, target_id: id } });
  } catch (err) {
    on ? STARS[kind].add(id) : STARS[kind].delete(id); // roll back
    renderAll();
    flash("Could not save that star: " + err.message);
  }
}
function starBtn(kind, id, label) {
  const on = isStar(kind, id);
  return `<button class="starbtn ${on ? "on" : ""}" data-sk="${esc(kind)}" data-skk="${esc(id)}" title="${label}">${on ? "★" : "☆"}</button>`;
}
function wireStars(root) {
  (root || document).querySelectorAll(".starbtn").forEach(b => {
    b.onclick = e => { e.preventDefault(); e.stopPropagation(); toggleStar(b.dataset.sk, b.dataset.skk); };
  });
}

/* ---------- submissions helpers ---------- */
const who = s => s.lead || s.created_by || "someone";
const addedBy = s => (s.created_by && s.created_by !== s.lead ? s.created_by : "");
function trackMatch(subTrack, msTrack) {
  if (!subTrack) return true;
  const b = (msTrack || "").toLowerCase();
  if (!b || /^main\b/.test(b)) return true;
  const a = subTrack.toLowerCase();
  if (b.includes(a) || a.includes(b)) return true;
  return a.split(/[/,;+]+/).some(t => { t = t.trim(); return t.length > 2 && b.includes(t); });
}
const subsFor = m => (LIFECYCLE.has(m._k) ? SUBS.filter(s => s.conference_id === m.conference_id && trackMatch(s.track, m.track)) : []);
function subChips(list) {
  if (!list.length) return "";
  const show = list.slice(0, 4), extra = list.length - show.length;
  return `<div class="subchips">` + show.map(s => `<button class="subchip" data-sid="${esc(s.id)}" title="${esc(s.abstract || "")}">
      <span class="dot s-${esc((s.status || "Idea").toLowerCase().replace(/[^a-z]/g, ""))}"></span>${esc(s.title || "Untitled")}${s.track ? ` <span class="t">${esc(s.track)}</span>` : ""}
      <span class="t">${esc(who(s))}${addedBy(s) ? " · via " + esc(addedBy(s)) : ""} · ${esc(s.status || "Idea")}</span>
    </button>`).join("") + (extra ? `<span class="subchip more">+${extra} more</span>` : "") + `</div>`;
}
function wireSubChips(root) {
  (root || document).querySelectorAll(".subchip[data-sid]").forEach(b => {
    b.onclick = e => { e.stopPropagation(); const s = SUBS.find(x => x.id === b.dataset.sid); if (s) openModal(s); };
  });
}

/* ---------- filter bar ---------- */
function renderFilterBar() {
  const counts = {};
  MSR.forEach(m => { if (timeOK(m.date)) counts[m._k] = (counts[m._k] || 0) + 1; });
  $("#kindchips").innerHTML = KINDS.map(([k, l]) =>
    `<button class="chip ${F.kinds.has(k) ? "on" : ""}" data-k="${k}">${l}<span class="n">${counts[k] || 0}</span></button>`).join("")
    + `<button class="chip plain" data-k="__all">All</button><button class="chip plain" data-k="__none">None</button>`;
  $$("#kindchips [data-k]").forEach(b => b.onclick = () => {
    const k = b.dataset.k;
    if (k === "__all") F.kinds = new Set(KINDS.map(x => x[0]));
    else if (k === "__none") F.kinds = new Set();
    else F.kinds.has(k) ? F.kinds.delete(k) : F.kinds.add(k);
    renderAll();
  });
  $("#venuechips").innerHTML = CONFS.map(c =>
    `<button class="chip ${F.venues.has(c.venue) ? "on" : ""}" data-vch="${esc(c.venue)}" title="${esc(c.name)}">${esc(c.venue)}</button>`).join("")
    + `<button class="chip plain" data-vch="__clear">All</button>`;
  $$("#venuechips [data-vch]").forEach(b => b.onclick = () => {
    const v = b.dataset.vch;
    if (v === "__clear") F.venues.clear();
    else F.venues.has(v) ? F.venues.delete(v) : F.venues.add(v);
    renderAll();
  });
  $$("[data-t]").forEach(b => b.classList.toggle("on", b.dataset.t === F.time));
  $("#starchip").classList.toggle("on", F.starred);
  const bits = [];
  if (F.venues.size) bits.push(F.venues.size + " conference" + (F.venues.size > 1 ? "s" : ""));
  if (F.kinds.size !== KINDS.length) bits.push(`${F.kinds.size} of ${KINDS.length} kinds`);
  if (F.q) bits.push(`"${F.q}"`);
  if (F.starred) bits.push("starred only");
  $("#filtersum").textContent = bits.length ? "Filtered: " + bits.join(" · ") : "Showing everything in the future";
}

/* ---------- deadlines ---------- */
function msFiltered() {
  return MSR.filter(m => venueOK(m.venue) && F.kinds.has(m._k) && timeOK(m.date)
    && qMatch(`${m.venue} ${m.track || ""} ${m.milestone || ""} ${m.notes || ""}`)
    && starOK(m.conference_id, m.id));
}
function renderStarSection() {
  const sm = MSR.filter(m => isStar("milestone", m.id));
  const sv = CONFS.filter(c => isStar("conference", c.id));
  if (!sm.length && !sv.length) { $("#starsec").innerHTML = ""; return; }
  const cards = sv.map(c => {
    const next = MSR.filter(m => m.conference_id === c.id && pd(m.date) >= today)[0];
    return `<div class="starcard"><div class="vrow"><div class="v">${esc(c.venue)}</div>${starBtn("conference", c.id, "Remove from starred")}</div>
      <div class="m">whole conference &middot; next: ${next ? esc(next.milestone) : "no dates announced"}</div>
      <div class="d"><span>${next ? fmt(next.date) : "—"}</span><span>${next ? rel(next.date) : ""}</span></div></div>`;
  }).concat(sm.map(m => `<div class="starcard"><div class="vrow"><div class="v">${esc(m.venue)}</div>${starBtn("milestone", m.id, "Remove from starred")}</div>
      <div class="m">${esc(m.milestone)}${m.track && !/^main\b/i.test(m.track) ? " · " + esc(m.track) : ""}</div>
      <div class="d"><span>${fmt(m.date)}</span><span>${rel(m.date)}</span></div></div>`));
  $("#starsec").innerHTML = '<h2 class="sectionhead">&#9733; Your starred items <span style="font-weight:500;text-transform:none;letter-spacing:0">— click a star to remove; these drive your calendar feed</span></h2>'
    + '<div class="strip" style="margin-bottom:26px">' + cards.join("") + "</div>";
  wireStars($("#starsec"));
}
function renderDeadlines() {
  renderStarSection();
  const picked = msFiltered();
  $("#mscount").textContent = `(${picked.length})`;
  let html = "", lastMonth = "";
  if (!picked.length) html = '<div class="empty" style="margin:12px">Nothing matches these filters.</div>';
  picked.forEach(m => {
    const d = pd(m.date);
    const key = `${MON[d.getMonth()]} ${d.getFullYear()}`;
    if (key !== lastMonth) {
      lastMonth = key;
      html += `<div style="padding:12px 16px 6px;font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-3);border-top:1px solid var(--line)">${key}</div>`;
    }
    const mainTrack = /^main\b/i.test(m.track || "");
    html += `<div class="mrow" style="padding:7px 16px;grid-template-columns:auto 92px 1fr auto;align-items:center;gap:8px">
      ${starBtn("milestone", m.id, "Star this milestone")}
      <div class="md">${fmtShort(m.date)}${m.end_date ? "–" + fmtShort(m.end_date) : ""}</div>
      <div><b>${esc(m.venue)}</b> &middot; ${esc(m.milestone)}${m.track && !mainTrack ? ` <span class="trk">${esc(m.track)}</span>` : ""} <span class="badge">${KINDLABEL[m._k]}</span>
        <div class="mt">${mainTrack ? esc(m.track) : ""}${m.time_basis && m.time_basis !== "Not specified" && m.time_basis !== "Event local dates" ? (mainTrack ? " · " : "") + esc(m.time_basis) : ""}${m.status && m.status !== "Published" ? ' · <span style="color:var(--warn)">' + esc(m.status) + "</span>" : ""}${m.notes ? " · " + esc(m.notes) : ""}</div>
      </div>
      <div><span class="pill p-${urgency(m.date)}">${rel(m.date) || "—"}</span></div>
      <div style="grid-column:3 / -1">${subChips(subsFor(m))}</div>
    </div>`;
  });
  $("#mstable").innerHTML = html;
  wireStars($("#mstable")); wireSubChips($("#mstable"));
}

/* ---------- conferences ---------- */
let cSort = "paper";
function confTimeOK(c) {
  const mine = MSR.filter(m => m.conference_id === c.id);
  const anyFuture = mine.some(m => pd(m.date) >= today);
  return F.time === "future" ? (anyFuture || !mine.length) : !anyFuture;
}
function confsFiltered() {
  return CONFS.filter(c => venueOK(c.venue) && confTimeOK(c) && (!F.starred || isStar("conference", c.id))
    && qMatch(`${c.venue} ${c.name} ${c.location || ""} ${c.emphasis || ""} ${c.fit || ""}`));
}
function confCard(c) {
  const u = c.paper_date ? urgency(c.paper_date) : "tbd";
  const pillText = !c.paper_date ? "deadline TBA" : (days(c.paper_date) < 0 ? "closed" : rel(c.paper_date));
  const rows = [["Abstract", c.abstract_date], ["Paper", c.paper_date], ["Notification", c.notify_date], ["Camera-ready", c.camera_date]]
    .map(([k, v]) => `<div class="row"><div class="k">${k}</div><div class="vv">${fmt(v)}</div><div class="c">${v ? rel(v) : ""}</div></div>`).join("");
  const ev = `<div class="row"><div class="k">Event</div><div class="vv">${c.start_date ? fmtShort(c.start_date) + (c.end_date ? " – " + fmt(c.end_date) : "") : "—"}</div><div class="c">${c.start_date ? rel(c.start_date) : ""}</div></div>`;
  const mine = SUBS.filter(s => s.conference_id === c.id);
  const ms = MSR.filter(m => m.conference_id === c.id && F.kinds.has(m._k) && timeOK(m.date));
  return `<article class="card">
    <div class="hd"><div class="t">
      <div class="venue">${esc(c.venue)}</div>
      <div class="fullname">${esc(c.name)}</div>
      <div class="loc">${esc(c.location || "Location TBA")}</div>
    </div>${starBtn("conference", c.id, "Star this conference")}<span class="pill p-${u}">${pillText}</span></div>
    <div class="dl">${rows}${ev}</div>
    <details class="more"><summary>Scope, fit and notes</summary><div class="mbody">
      <p><b>Emphasis.</b> ${esc(c.emphasis || "—")}</p>
      <p><b>Worth submitting when.</b> ${esc(c.fit || "—")}</p>
      ${c.notes ? `<p><b>Notes.</b> ${esc(c.notes)}</p>` : ""}
      <p><b>Status.</b> ${esc(c.status || "—")}</p>
    </div></details>
    ${ms.length ? `<details class="more"><summary>Milestones matching your filters (${ms.length})</summary><div class="mbody"><div class="mlist">
      ${ms.map(m => `<div class="mrow"><div class="md">${fmtShort(m.date)}${m.end_date ? "–" + fmtShort(m.end_date) : ""}</div><div><div>${esc(m.milestone)} <span class="badge">${KINDLABEL[m._k]}</span></div><div class="mt">${esc(m.track || "")}</div></div></div>`).join("")}
    </div></div></details>` : ""}
    <div class="ft">
      ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener" class="btn ghost sm">Official page ↗</a>` : ""}
      <button class="btn ghost sm reg" data-cid="${esc(c.id)}">+ Register idea</button>
      <div class="spacer"></div>
      <span class="badge">${mine.length} idea${mine.length === 1 ? "" : "s"}</span>
    </div>
    ${mine.length ? `<div style="padding:0 16px 12px">${subChips(mine)}</div>` : ""}
  </article>`;
}
function renderConfs() {
  const far = "9999-99-99";
  const list = confsFiltered().slice().sort((a, b) => {
    if (cSort === "venue") return a.venue.localeCompare(b.venue);
    if (cSort === "event") return (a.start_date || far).localeCompare(b.start_date || far);
    return (a.paper_date || far).localeCompare(b.paper_date || far);
  });
  $("#ccount").textContent = `${list.length} conference${list.length === 1 ? "" : "s"}`;
  $("#cgrid").innerHTML = list.length ? list.map(confCard).join("") : '<div class="empty">No conferences match these filters.</div>';
  $$(".reg").forEach(b => b.onclick = () => openModal(null, b.dataset.cid));
  wireStars($("#cgrid")); wireSubChips($("#cgrid"));
}

/* ---------- timeline ---------- */
const T0 = new Date(2026, 6, 1), T1 = new Date(2027, 11, 31);
const SPAN = T1 - T0;
const pct = d => Math.max(0, Math.min(100, (pd(d) - T0) / SPAN * 100));
function renderTimeline() {
  const months = []; const d = new Date(T0);
  while (d <= T1) { months.push(new Date(d)); d.setMonth(d.getMonth() + 1); }
  const head = `<div class="tlhead"><div class="lbl"></div><div class="tlmonths">${months.map((m, i) =>
    `<div class="tlm ${m.getMonth() % 2 ? "q" : ""}">${MON[m.getMonth()]}${(m.getMonth() === 0 || i === 0) ? "<br><span style='opacity:.6'>" + m.getFullYear() + "</span>" : ""}</div>`).join("")}</div></div>`;
  const grid = `<div class="tlgrid">${months.map(() => "<span></span>").join("")}</div>`;
  const tdy = (today >= T0 && today <= T1) ? `<div class="todayline" style="left:${(today - T0) / SPAN * 100}%"></div>` : "";
  const rows = confsFiltered().map(c => {
    const marks = MSR.filter(m => m.conference_id === c.id && F.kinds.has(m._k) && m._k !== "event")
      .map(m => `<div class="mk ${m._k}" style="left:${pct(m.date)}%" title="${esc(m.milestone)} — ${fmt(m.date)}"></div>`).join("");
    let bar = "";
    if (c.start_date && F.kinds.has("event")) {
      const l = pct(c.start_date), r = pct(c.end_date || c.start_date);
      bar = `<div class="ev" style="left:${l}%;width:${Math.max(r - l, 0.6)}%" title="${esc(c.venue)} ${fmt(c.start_date)}"></div>`;
    }
    return `<div class="tlrow"><div class="lbl">${esc(c.venue)}</div><div class="tltrack">${grid}${tdy}${bar}${marks}</div></div>`;
  }).join("");
  $("#tl").innerHTML = head + (rows || '<div class="empty" style="margin:16px">Nothing matches these filters.</div>');
  $("#tllegend").innerHTML = KINDS.filter(([k]) => k !== "event").map(([k, l]) =>
    `<span style="opacity:${F.kinds.has(k) ? 1 : .35}"><i class="mk ${k}" style="position:static;transform:rotate(45deg)"></i>${l}</span>`).join("")
    + `<span style="opacity:${F.kinds.has("event") ? 1 : .35}"><i style="background:var(--accent-soft);border:1px solid var(--accent);transform:none;border-radius:3px;width:16px"></i>Event</span>`;
}

/* ---------- map ---------- */
function siteGroups() {
  const g = new Map();
  confsFiltered().forEach(c => {
    const site = c.site_key;
    if (!site || !MAP || !MAP.pts[site]) return;
    if (!g.has(site)) g.set(site, { site, xy: MAP.pts[site], confs: [] });
    g.get(site).confs.push(c);
  });
  const arr = [...g.values()];
  arr.forEach(s => { s.first = s.confs.map(c => c.start_date).filter(Boolean).sort()[0] || "9999"; });
  arr.sort((a, b) => a.first.localeCompare(b.first));
  arr.forEach((s, i) => s.n = i + 1);
  return arr;
}
function spread(sites) {
  const p = sites.map(s => ({ s, x: s.xy[0], y: s.xy[1], lx: s.xy[0], ly: s.xy[1] }));
  const MIN = 18;
  for (let it = 0; it < 120; it++) {
    let moved = false;
    for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) {
      const a = p[i], b = p[j];
      let dx = b.lx - a.lx, dy = b.ly - a.ly, d = Math.hypot(dx, dy);
      if (d < MIN) {
        if (d < 0.01) { dx = (i % 2 ? 1 : -1); dy = (j % 2 ? 1 : -1); d = Math.hypot(dx, dy); }
        const k = (MIN - d) / 2 / d;
        a.lx -= dx * k; a.ly -= dy * k; b.lx += dx * k; b.ly += dy * k; moved = true;
      }
    }
    if (!moved) break;
  }
  p.forEach(o => { o.lx = Math.max(12, Math.min(MAP.w - 12, o.lx)); o.ly = Math.max(12, Math.min(MAP.h - 12, o.ly)); });
  return p;
}
function renderMap() {
  if (!MAP) return;
  const sites = siteGroups(), laid = spread(sites);
  const sel = s => s.confs.some(c => F.venues.has(c.venue));
  const starred = s => s.confs.some(c => isStar("conference", c.id));
  $("#mapbox").innerHTML = `<svg viewBox="0 0 ${MAP.w} ${MAP.h}" role="img" aria-label="World map of conference locations">
    <path d="${MAP.sphere}" fill="var(--ocean)"></path>
    <path d="${MAP.graticule}" fill="none" stroke="var(--landline)" stroke-width="0.3" opacity="0.45"></path>
    <path d="${MAP.land}" fill="var(--land)"></path>
    <path d="${MAP.borders}" fill="none" stroke="var(--landline)" stroke-width="0.5"></path>
    ${laid.map(o => { const s = o.s, off = Math.hypot(o.lx - o.x, o.ly - o.y) > 2;
      return `<g class="site ${sel(s) ? "sel" : ""} ${starred(s) ? "star" : ""}" data-site="${esc(s.site)}">
      ${off ? `<line class="leader" x1="${o.x}" y1="${o.y}" x2="${o.lx}" y2="${o.ly}"></line><circle class="anchor" cx="${o.x}" cy="${o.y}" r="1.8"></circle>` : ""}
      <circle class="ring" cx="${o.lx}" cy="${o.ly}" r="12"></circle>
      <circle class="dot" cx="${o.lx}" cy="${o.ly}" r="7.5"></circle>
      <text x="${o.lx}" y="${o.ly}">${s.n}</text>
      <circle class="hit" cx="${o.lx}" cy="${o.ly}" r="13"><title>${esc(s.site)}: ${esc(s.confs.map(c => c.venue).join(", "))}</title></circle>
    </g>`; }).join("")}
  </svg>`;
  $("#sitelist").innerHTML = sites.length ? sites.map(s => `<div class="srow ${sel(s) ? "sel" : ""}" data-site="${esc(s.site)}">
      <div class="num">${s.n}</div>
      <div><div class="city">${esc(s.site)}</div>
        <div class="vs">${esc(s.confs.map(c => c.venue).join(" · "))}</div>
        <div class="dt">${s.first !== "9999" ? fmt(s.first) + " · " + rel(s.first) : "dates TBA"}</div></div>
    </div>`).join("") : '<div class="empty" style="margin:12px">No located conferences match these filters.</div>';
  const un = confsFiltered().filter(c => !c.site_key || !MAP.pts[c.site_key]);
  $("#unloc").innerHTML = un.length ? `<b style="color:var(--text-2)">No location announced:</b> ${un.map(c => esc(c.venue) + " (" + esc(c.location || "—") + ")").join(", ")}` : "";
  const pick = site => {
    const s = sites.find(x => x.site === site); if (!s) return;
    const all = s.confs.every(c => F.venues.has(c.venue));
    s.confs.forEach(c => all ? F.venues.delete(c.venue) : F.venues.add(c.venue));
    renderAll();
  };
  $$("#mapbox .site").forEach(g => g.onclick = () => pick(g.dataset.site));
  $$("#sitelist .srow").forEach(r => r.onclick = () => pick(r.dataset.site));
}

/* ---------- submissions ---------- */
let editing = null;
function subCard(s) {
  return `<div class="sub-card" data-id="${esc(s.id)}">
    <div class="st">${esc(s.title || "Untitled")}</div>
    <div class="sv">${esc(s.venue || "No venue yet")}${s.track ? " · " + esc(s.track) : ""}</div>
    <div class="sv" style="color:var(--text-3)">${esc(who(s))}${s.coauthors ? ", " + esc(s.coauthors) : ""}${addedBy(s) ? ` <span style="opacity:.75">· added by ${esc(addedBy(s))}</span>` : ""}</div>
    <div class="sm">${s.project ? `<span class="tagdot">${esc(s.project)}</span>` : "<span></span>"}
      <span>${s.links && s.links.length ? s.links.length + " link" + (s.links.length > 1 ? "s" : "") : ""}</span></div>
  </div>`;
}
function renderSubs() {
  const list = SUBS.filter(s => {
    if (s.conference_id && !venueOK(s.venue)) return false;
    if (!s.conference_id && F.venues.size) return false;
    if (F.starred && s.conference_id && !isStar("conference", s.conference_id)) return false;
    return qMatch(JSON.stringify(s));
  });
  $("#subcount").textContent = SUBS.length || "";
  $("#scount").textContent = `${list.length} of ${SUBS.length} entr${SUBS.length === 1 ? "y" : "ies"}`;
  $("#scols").innerHTML = STATUSES.map(st => {
    const items = list.filter(s => (s.status || "Idea") === st);
    return `<div class="col"><h3><span>${st}</span><span>${items.length}</span></h3>
      ${items.length ? items.map(subCard).join("") : '<div class="empty" style="padding:9px;font-size:11.5px">—</div>'}</div>`;
  }).join("");
  $$(".sub-card").forEach(c => c.onclick = () => openModal(SUBS.find(s => s.id === c.dataset.id)));
}
function linkRow(v) {
  const d = document.createElement("div");
  d.className = "linkrow";
  d.innerHTML = `<input type="url" placeholder="https://…" value="${esc(v || "")}"><button class="btn ghost sm" type="button">×</button>`;
  d.querySelector("button").onclick = () => d.remove();
  return d;
}
function openModal(s, presetConfId) {
  editing = s || null;
  $("#mtitle").textContent = s ? "Edit entry" : "Register an idea";
  $("#f-title").value = s?.title || ""; $("#f-track").value = s?.track || "";
  $("#f-lead").value = s?.lead || (s ? "" : (ME?.name || "")); $("#f-coauthors").value = s?.coauthors || "";
  $("#f-status").value = s?.status || "Idea"; $("#f-project").value = s?.project || "";
  $("#f-abstract").value = s?.abstract || ""; $("#f-notes").value = s?.notes || "";
  $("#f-venue").value = s?.conference_id || presetConfId || "";
  const lw = $("#f-links"); lw.innerHTML = "";
  (s?.links?.length ? s.links : [""]).forEach(l => lw.appendChild(linkRow(l)));
  $("#delbtn").classList.toggle("hidden", !s);
  $("#meta").textContent = s ? [addedBy(s) ? "added by " + addedBy(s) : "", s.updated_at ? "updated " + new Date(s.updated_at).toLocaleDateString() : ""].filter(Boolean).join(" · ") : "";
  $("#scrim").classList.add("on"); $("#f-title").focus();
}
const closeModal = () => { $("#scrim").classList.remove("on"); editing = null; };
async function saveSub() {
  const links = Array.from($("#f-links").querySelectorAll("input")).map(i => i.value.trim()).filter(Boolean);
  const body = {
    title: $("#f-title").value.trim(),
    conference_id: $("#f-venue").value || null,
    track: $("#f-track").value.trim(),
    lead: $("#f-lead").value.trim(),
    coauthors: $("#f-coauthors").value.trim(),
    status: $("#f-status").value,
    project: $("#f-project").value.trim(),
    abstract: $("#f-abstract").value.trim(),
    notes: $("#f-notes").value.trim(),
    links,
  };
  if (!body.title) { $("#f-title").focus(); return; }
  try {
    const saved = editing
      ? await api(`/api/submissions/${editing.id}`, { method: "PUT", body })
      : await api("/api/submissions", { method: "POST", body });
    const i = SUBS.findIndex(s => s.id === saved.id);
    if (i >= 0) SUBS[i] = saved; else SUBS.unshift(saved);
    closeModal(); renderAll(); flash("Saved");
  } catch (err) { flash("Could not save: " + err.message); }
}

/* ---------- admin ---------- */
const DATE_FIELDS = [["abstract_date","Abstract"],["paper_date","Paper"],["notify_date","Notify"],["camera_date","Camera"],["start_date","Start"],["end_date","End"]];
function renderAdmin() {
  if (!ME?.is_admin) { $("#adminlist").innerHTML = ""; return; }
  $("#adminlist").innerHTML = CONFS.map(c => `<details class="panel" style="margin-bottom:10px" data-cid="${esc(c.id)}">
    <summary style="padding:11px 14px;cursor:pointer;font-weight:600">${esc(c.venue)} <span style="font-weight:400;color:var(--text-3)">— ${esc(c.name)}</span></summary>
    <div style="padding:4px 14px 14px">
      <table class="edit"><tbody>
        <tr><th>Venue</th><td><input data-f="venue" value="${esc(c.venue)}"></td><th>Name</th><td><input data-f="name" value="${esc(c.name)}"></td></tr>
        <tr><th>Location</th><td><input data-f="location" value="${esc(c.location)}"></td><th>Map site key</th><td><input data-f="site_key" value="${esc(c.site_key || "")}" placeholder="e.g. Basel, Switzerland"></td></tr>
        <tr><th>URL</th><td><input data-f="url" value="${esc(c.url)}"></td><th>Status</th><td><input data-f="status" value="${esc(c.status)}"></td></tr>
        ${DATE_FIELDS.map(([f, l]) => `<tr><th>${l}</th><td colspan="3"><input type="date" data-f="${f}" value="${c[f] || ""}"></td></tr>`).join("")}
        <tr><th>Emphasis</th><td colspan="3"><input data-f="emphasis" value="${esc(c.emphasis)}"></td></tr>
        <tr><th>Fit</th><td colspan="3"><input data-f="fit" value="${esc(c.fit)}"></td></tr>
        <tr><th>Notes</th><td colspan="3"><input data-f="notes" value="${esc(c.notes)}"></td></tr>
      </tbody></table>
      <div class="rowbtns" style="margin:10px 0 16px">
        <button class="btn sm saveconf">Save conference</button>
        <button class="btn danger sm delconf">Delete</button>
      </div>
      <b style="font-size:12px">Milestones</b>
      <table class="edit"><thead><tr><th style="width:120px">Date</th><th style="width:120px">End</th><th>Milestone</th><th>Track</th><th style="width:150px">Time basis</th><th style="width:90px"></th></tr></thead><tbody>
      ${MSR.filter(m => m.conference_id === c.id).map(m => `<tr data-mid="${esc(m.id)}">
        <td><input type="date" data-f="date" value="${m.date}"></td>
        <td><input type="date" data-f="end_date" value="${m.end_date || ""}"></td>
        <td><input data-f="milestone" value="${esc(m.milestone)}"></td>
        <td><input data-f="track" value="${esc(m.track)}"></td>
        <td><input data-f="time_basis" value="${esc(m.time_basis)}"></td>
        <td class="rowbtns"><button class="btn ghost sm savems">Save</button><button class="btn danger sm delms">×</button></td></tr>`).join("")}
        <tr data-new="1">
          <td><input type="date" data-f="date"></td><td><input type="date" data-f="end_date"></td>
          <td><input data-f="milestone" placeholder="New milestone"></td><td><input data-f="track"></td>
          <td><input data-f="time_basis" placeholder="23:59 AoE (UTC−12)"></td>
          <td class="rowbtns"><button class="btn sm addms">Add</button></td></tr>
      </tbody></table>
    </div></details>`).join("");
  wireAdmin();
}
function fieldsOf(scope) {
  const out = {};
  scope.querySelectorAll("[data-f]").forEach(i => { out[i.dataset.f] = i.value || (i.type === "date" ? null : ""); });
  return out;
}
function wireAdmin() {
  $$("#adminlist details").forEach(det => {
    const cid = det.dataset.cid;
    const table = det.querySelector("table.edit");
    det.querySelector(".saveconf").onclick = async () => {
      try { const saved = await api(`/api/conferences/${cid}`, { method: "PUT", body: fieldsOf(table) });
        Object.assign(CONFS.find(c => c.id === cid), saved); flash("Conference saved"); renderAll(); }
      catch (err) { flash("Save failed: " + err.message); }
    };
    det.querySelector(".delconf").onclick = async () => {
      if (!confirmInline(det, "Delete this conference and all its milestones?")) return;
      try { await api(`/api/conferences/${cid}`, { method: "DELETE" }); await reload(); flash("Deleted"); }
      catch (err) { flash("Delete failed: " + err.message); }
    };
    det.querySelectorAll("tr[data-mid]").forEach(tr => {
      tr.querySelector(".savems").onclick = async () => {
        try { await api(`/api/milestones/${tr.dataset.mid}`, { method: "PUT", body: fieldsOf(tr) }); await reload(); flash("Milestone saved"); }
        catch (err) { flash("Save failed: " + err.message); }
      };
      tr.querySelector(".delms").onclick = async () => {
        try { await api(`/api/milestones/${tr.dataset.mid}`, { method: "DELETE" }); await reload(); flash("Milestone deleted"); }
        catch (err) { flash("Delete failed: " + err.message); }
      };
    });
    const newRow = det.querySelector("tr[data-new]");
    newRow.querySelector(".addms").onclick = async () => {
      const body = fieldsOf(newRow);
      if (!body.date) { flash("A date is required"); return; }
      try { await api(`/api/conferences/${cid}/milestones`, { method: "POST", body }); await reload(); flash("Milestone added"); }
      catch (err) { flash("Add failed: " + err.message); }
    };
  });
}
function confirmInline(el, message) { return window.confirm(message); }

/* ---------- render ---------- */
function renderAll() {
  renderFilterBar(); renderDeadlines(); renderConfs();
  renderTimeline(); renderMap(); renderSubs(); renderAdmin();
}

/* ---------- bootstrap ---------- */
function indexData(data) {
  CONFS = data.conferences;
  CONF_BY_ID = new Map(CONFS.map(c => [c.id, c]));
  MSR = data.milestones.map(m => ({ ...m, _k: kindOf(m) }))
    .sort((a, b) => a.date.localeCompare(b.date));
  SUBS = data.submissions;
  STARS = { conference: new Set(data.stars.conference || []), milestone: new Set(data.stars.milestone || []) };
  ME = data.me;
}
async function reload() { indexData(await api("/api/bootstrap")); renderAll(); }

async function start() {
  let data;
  try { data = await api("/api/bootstrap"); } catch (err) { return; }
  if (data.me.auth_enabled && !data.me.authenticated) { showSignin(); return; }
  indexData(data);
  $("#root").hidden = false;
  $("#apptitle").textContent = data.app_name;
  document.title = data.app_name;
  $("#who").innerHTML = ME.authenticated
    ? `<b>${esc(ME.name || ME.email)}</b>${ME.is_admin ? " · admin" : ""}<br><a href="/auth/logout">Sign out</a>`
    : "Not signed in";
  $("#admintab").classList.toggle("hidden", !ME.is_admin);
  $("#feedurl").textContent = ME.feed_url || "—";
  $("#allfeedurl").textContent = location.origin + "/feeds/all.ics";

  $("#f-venue").innerHTML = '<option value="">— pick a venue —</option>'
    + CONFS.map(c => `<option value="${esc(c.id)}">${esc(c.venue)} — ${esc(c.name)}</option>`).join("");
  $("#f-status").innerHTML = STATUSES.map(s => `<option>${s}</option>`).join("");
  $("#projlist").innerHTML = PROJECTS.map(p => `<option value="${esc(p)}">`).join("");

  try { MAP = await (await fetch("/static/map.json")).json(); } catch (e) { MAP = null; }
  renderAll();
}

/* ---------- events ---------- */
$$(".tab").forEach(t => t.onclick = () => {
  $$(".tab").forEach(x => x.classList.remove("on")); t.classList.add("on");
  $$(".view").forEach(v => v.classList.remove("on")); $("#v-" + t.dataset.v).classList.add("on");
});
$("#gsearch").oninput = e => { F.q = e.target.value.trim().toLowerCase(); renderAll(); };
$$("[data-t]").forEach(b => b.onclick = () => { F.time = b.dataset.t; renderAll(); });
$("#starchip").onclick = () => { F.starred = !F.starred; renderAll(); };
$("#csort").onchange = e => { cSort = e.target.value; renderConfs(); };
$("#addbtn").onclick = () => openModal(null);
$("#cancelbtn").onclick = closeModal;
$("#scrim").onclick = e => { if (e.target === $("#scrim")) closeModal(); };
$("#addlink").onclick = () => $("#f-links").appendChild(linkRow(""));
$("#savebtn").onclick = saveSub;
$("#delbtn").onclick = async () => {
  if (!editing) return;
  try { await api(`/api/submissions/${editing.id}`, { method: "DELETE" });
    SUBS = SUBS.filter(s => s.id !== editing.id); closeModal(); renderAll(); flash("Deleted"); }
  catch (err) { flash("Could not delete: " + err.message); }
};
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); flash("Copied"); }
  catch (e) { flash("Copy failed — select the URL and copy manually"); }
}
$("#copyfeed").onclick = () => copyText($("#feedurl").textContent);
$("#copyall").onclick = () => copyText($("#allfeedurl").textContent);
$("#rotatefeed").onclick = async () => {
  if (!window.confirm("Rotate the URL? Calendars already subscribed to the old one will stop updating.")) return;
  try { const r = await api("/api/feed-token/rotate", { method: "POST" });
    $("#feedurl").textContent = r.feed_url; flash("New calendar URL issued"); }
  catch (err) { flash("Could not rotate: " + err.message); }
};
$("#newconf").onclick = async () => {
  const venue = window.prompt("Short venue code (e.g. ESWC)");
  if (!venue) return;
  try { await api("/api/conferences", { method: "POST", body: { venue, name: venue } }); await reload(); flash("Conference created"); }
  catch (err) { flash("Could not create: " + err.message); }
};

start();
