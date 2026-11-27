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

/* A series (ESWC) has yearly editions (ESWC 2027). CONFS are editions; each
   series names the edition to show by default (current_edition_id): the next one
   still taking submissions, else the latest. SEL holds a year the user picked. */
let SERIES = [], CONFS = [], MSR = [], SUBS = [], MAP = null, ME = null;
let STARS = { series: new Set(), milestone: new Set() };
let CONF_BY_ID = new Map(), SERIES_BY_ID = new Map();
const SEL = new Map();

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
    try {
      const d = (await res.json()).detail;
      // 422s list each invalid field: show "url: String should match pattern …"
      detail = Array.isArray(d) ? d.map(x => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ") : (d || detail);
    } catch (e) { /* non-JSON error */ }
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
const starOK = (seriesId, msId) => !F.starred || isStar("series", seriesId) || (msId && isStar("milestone", msId));

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
  $("#venuechips").innerHTML = SERIES.map(s =>
    `<button class="chip ${F.venues.has(s.acronym) ? "on" : ""}" data-vch="${esc(s.acronym)}" title="${esc(s.name)}">${esc(s.acronym)}</button>`).join("")
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
    && qMatch(`${m.label} ${m.track || ""} ${m.milestone || ""} ${m.notes || ""}`)
    && starOK(m.series_id, m.id));
}
function renderStarSection() {
  const sm = MSR.filter(m => isStar("milestone", m.id));
  const sv = SERIES.filter(s => isStar("series", s.id));
  if (!sm.length && !sv.length) { $("#starsec").innerHTML = ""; return; }
  const cards = sv.map(s => {
    const next = MSR.filter(m => m.series_id === s.id && pd(m.date) >= today)[0];
    return `<div class="starcard"><div class="vrow"><div class="v">${esc(s.acronym)}</div>${starBtn("series", s.id, "Remove from starred")}</div>
      <div class="m">every edition &middot; next: ${next ? esc(next.label + " · " + next.milestone) : "no dates announced"}</div>
      <div class="d"><span>${next ? fmt(next.date) : "—"}</span><span>${next ? rel(next.date) : ""}</span></div></div>`;
  }).concat(sm.map(m => `<div class="starcard"><div class="vrow"><div class="v">${esc(m.label)}</div>${starBtn("milestone", m.id, "Remove from starred")}</div>
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
      <div><b>${esc(m.label)}</b> &middot; ${esc(m.milestone)}${m.track && !mainTrack ? ` <span class="trk">${esc(m.track)}</span>` : ""} <span class="badge">${KINDLABEL[m._k]}</span>
        <div class="mt">${mainTrack ? esc(m.track) : ""}${m.time_basis && m.time_basis !== "Not specified" && m.time_basis !== "Event local dates" ? (mainTrack ? " · " : "") + esc(m.time_basis) : ""}${m.status && m.status !== "Published" ? ' · <span style="color:var(--warn)">' + esc(m.status) + "</span>" : ""}${m.notes ? " · " + esc(m.notes) : ""}</div>
      </div>
      <div><span class="pill p-${urgency(m.date)}">${rel(m.date) || "—"}</span></div>
      <div style="grid-column:3 / -1">${subChips(subsFor(m))}</div>
    </div>`;
  });
  $("#mstable").innerHTML = html;
  wireStars($("#mstable")); wireSubChips($("#mstable"));
}

/* ---------- conferences (one card per series) ---------- */
let cSort = "paper";
const shownEdition = s => CONF_BY_ID.get(SEL.get(s.id) || s.current_edition_id);
/* "future": every series, at its next edition (or the year picked on its card).
   "past": every edition with nothing left ahead, one card each. */
function confsFiltered() {
  let items;
  if (F.time === "future") {
    items = SERIES.map(s => ({ s, c: shownEdition(s) })).filter(x => x.c);
  } else {
    items = CONFS.filter(c => {
      const mine = MSR.filter(m => m.conference_id === c.id);
      return mine.length && !mine.some(m => pd(m.date) >= today);
    }).map(c => ({ s: SERIES_BY_ID.get(c.series_id), c }));
  }
  return items.filter(({ s, c }) => s && venueOK(s.acronym) && (!F.starred || isStar("series", s.id))
    && qMatch(`${c.label} ${s.name} ${c.location || ""} ${s.emphasis || ""} ${s.fit || ""}`));
}
function editionNotice(s, c) {
  if (F.time !== "future" || s.state === "open" || c.id !== s.current_edition_id) return "";
  const msg = s.state === "closed"
    ? `Submissions for ${esc(c.label)} have closed; the next edition isn't in the tracker yet.`
    : `Submission deadlines for ${esc(c.label)} aren't announced yet.`;
  return `<div class="notice">${msg}</div>`;
}
function yearChips(s, c) {
  if (s.years.length < 2) return "";
  const eds = CONFS.filter(e => e.series_id === s.id).sort((a, b) => a.year - b.year);
  return `<div class="years">${eds.map(e => `<button class="chip ${e.id === c.id ? "on" : ""}" data-sel-s="${esc(s.id)}" data-sel-c="${esc(e.id)}"
    title="${e.id === s.current_edition_id ? "the next edition" : ""}">${e.year}${e.id === s.current_edition_id ? " •" : ""}</button>`).join("")}</div>`;
}
function confCard({ s, c }) {
  const u = c.paper_date ? urgency(c.paper_date) : "tbd";
  const pillText = !c.paper_date ? "deadline TBA" : (days(c.paper_date) < 0 ? "closed" : rel(c.paper_date));
  const rows = [["Abstract", c.abstract_date], ["Paper", c.paper_date], ["Notification", c.notify_date], ["Camera-ready", c.camera_date]]
    .map(([k, v]) => `<div class="row"><div class="k">${k}</div><div class="vv">${fmt(v)}</div><div class="c">${v ? rel(v) : ""}</div></div>`).join("");
  const ev = `<div class="row"><div class="k">Event</div><div class="vv">${c.start_date ? fmtShort(c.start_date) + (c.end_date ? " – " + fmt(c.end_date) : "") : "—"}</div><div class="c">${c.start_date ? rel(c.start_date) : ""}</div></div>`;
  const mine = SUBS.filter(x => x.conference_id === c.id);
  const ms = MSR.filter(m => m.conference_id === c.id && F.kinds.has(m._k) && timeOK(m.date));
  const page = [c.url, s.homepage].find(safeUrl) || "";
  return `<article class="card">
    <div class="hd"><div class="t">
      <div class="venue">${esc(c.label)}</div>
      <div class="fullname">${esc(s.name)}</div>
      <div class="loc">${esc(c.location || "Location TBA")}</div>
    </div>${starBtn("series", s.id, "Star this series (every edition)")}<span class="pill p-${u}">${pillText}</span></div>
    ${yearChips(s, c)}${editionNotice(s, c)}
    <div class="dl">${rows}${ev}</div>
    <details class="more"><summary>Scope, fit and notes</summary><div class="mbody">
      <p><b>Emphasis.</b> ${esc(s.emphasis || "—")}</p>
      <p><b>Worth submitting when.</b> ${esc(s.fit || "—")}</p>
      ${s.notes ? `<p><b>About the series.</b> ${esc(s.notes)}</p>` : ""}
      ${c.notes ? `<p><b>Notes on ${esc(c.label)}.</b> ${esc(c.notes)}</p>` : ""}
      <p><b>Status.</b> ${esc(c.status || "—")}</p>
    </div></details>
    ${ms.length ? `<details class="more"><summary>Milestones matching your filters (${ms.length})</summary><div class="mbody"><div class="mlist">
      ${ms.map(m => `<div class="mrow"><div class="md">${fmtShort(m.date)}${m.end_date ? "–" + fmtShort(m.end_date) : ""}</div><div><div>${esc(m.milestone)} <span class="badge">${KINDLABEL[m._k]}</span></div><div class="mt">${esc(m.track || "")}</div></div></div>`).join("")}
    </div></div></details>` : ""}
    <div class="ft">
      ${page ? `<a href="${esc(page)}" target="_blank" rel="noopener" class="btn ghost sm">Official page ↗</a>` : ""}
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
    if (cSort === "venue") return a.s.acronym.localeCompare(b.s.acronym) || a.c.year - b.c.year;
    if (cSort === "event") return (a.c.start_date || far).localeCompare(b.c.start_date || far);
    return (a.c.paper_date || far).localeCompare(b.c.paper_date || far);
  });
  $("#ccount").textContent = `${list.length} ${F.time === "future" ? "series" : "past edition" + (list.length === 1 ? "" : "s")}`;
  $("#cgrid").innerHTML = list.length ? list.map(confCard).join("") : '<div class="empty">No conferences match these filters.</div>';
  $$(".reg").forEach(b => b.onclick = () => openModal(null, b.dataset.cid));
  $$("#cgrid [data-sel-s]").forEach(b => b.onclick = () => { SEL.set(b.dataset.selS, b.dataset.selC); renderAll(); });
  wireStars($("#cgrid")); wireSubChips($("#cgrid"));
}

/* ---------- timeline ---------- */
/* A rolling window: three months back, eighteen ahead, so 2028 appears as it nears. */
const T0 = new Date(today.getFullYear(), today.getMonth() - 3, 1);
const T1 = new Date(today.getFullYear(), today.getMonth() + 19, 0);
const SPAN = T1 - T0;
const pct = d => Math.max(0, Math.min(100, (pd(d) - T0) / SPAN * 100));
function renderTimeline() {
  const months = []; const d = new Date(T0);
  while (d <= T1) { months.push(new Date(d)); d.setMonth(d.getMonth() + 1); }
  const head = `<div class="tlhead"><div class="lbl"></div><div class="tlmonths">${months.map((m, i) =>
    `<div class="tlm ${m.getMonth() % 2 ? "q" : ""}">${MON[m.getMonth()]}${(m.getMonth() === 0 || i === 0) ? "<br><span style='opacity:.6'>" + m.getFullYear() + "</span>" : ""}</div>`).join("")}</div></div>`;
  const grid = `<div class="tlgrid">${months.map(() => "<span></span>").join("")}</div>`;
  const tdy = (today >= T0 && today <= T1) ? `<div class="todayline" style="left:${(today - T0) / SPAN * 100}%"></div>` : "";
  const rows = confsFiltered().map(({ c }) => {
    const marks = MSR.filter(m => m.conference_id === c.id && F.kinds.has(m._k) && m._k !== "event")
      .map(m => `<div class="mk ${m._k}" style="left:${pct(m.date)}%" title="${esc(m.milestone)} — ${fmt(m.date)}"></div>`).join("");
    let bar = "";
    if (c.start_date && F.kinds.has("event")) {
      const l = pct(c.start_date), r = pct(c.end_date || c.start_date);
      bar = `<div class="ev" style="left:${l}%;width:${Math.max(r - l, 0.6)}%" title="${esc(c.label)} ${fmt(c.start_date)}"></div>`;
    }
    return `<div class="tlrow"><div class="lbl">${esc(c.label)}</div><div class="tltrack">${grid}${tdy}${bar}${marks}</div></div>`;
  }).join("");
  $("#tl").innerHTML = head + (rows || '<div class="empty" style="margin:16px">Nothing matches these filters.</div>');
  $("#tllegend").innerHTML = KINDS.filter(([k]) => k !== "event").map(([k, l]) =>
    `<span style="opacity:${F.kinds.has(k) ? 1 : .35}"><i class="mk ${k}" style="position:static;transform:rotate(45deg)"></i>${l}</span>`).join("")
    + `<span style="opacity:${F.kinds.has("event") ? 1 : .35}"><i style="background:var(--accent-soft);border:1px solid var(--accent);transform:none;border-radius:3px;width:16px"></i>Event</span>`;
}

/* ---------- map ---------- */
function siteGroups() {
  const g = new Map();
  confsFiltered().forEach(({ c }) => {
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
  const starred = s => s.confs.some(c => isStar("series", c.series_id));
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
      <circle class="hit" cx="${o.lx}" cy="${o.ly}" r="13"><title>${esc(s.site)}: ${esc(s.confs.map(c => c.label).join(", "))}</title></circle>
    </g>`; }).join("")}
  </svg>`;
  $("#sitelist").innerHTML = sites.length ? sites.map(s => `<div class="srow ${sel(s) ? "sel" : ""}" data-site="${esc(s.site)}">
      <div class="num">${s.n}</div>
      <div><div class="city">${esc(s.site)}</div>
        <div class="vs">${esc(s.confs.map(c => c.label).join(" · "))}</div>
        <div class="dt">${s.first !== "9999" ? fmt(s.first) + " · " + rel(s.first) : "dates TBA"}</div></div>
    </div>`).join("") : '<div class="empty" style="margin:12px">No located conferences match these filters.</div>';
  const un = confsFiltered().map(x => x.c).filter(c => !c.site_key || !MAP.pts[c.site_key]);
  $("#unloc").innerHTML = un.length ? `<b style="color:var(--text-2)">No location announced:</b> ${un.map(c => esc(c.label) + " (" + esc(c.location || "—") + ")").join(", ")}` : "";
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
    <div class="sv">${esc(s.edition || "No venue yet")}${s.track ? " · " + esc(s.track) : ""}</div>
    <div class="sv" style="color:var(--text-3)">${esc(who(s))}${s.coauthors ? ", " + esc(s.coauthors) : ""}${addedBy(s) ? ` <span style="opacity:.75">· added by ${esc(addedBy(s))}</span>` : ""}</div>
    <div class="sm">${s.project ? `<span class="tagdot">${esc(s.project)}</span>` : "<span></span>"}
      <span>${s.links && s.links.length ? s.links.length + " link" + (s.links.length > 1 ? "s" : "") : ""}</span></div>
  </div>`;
}
function renderSubs() {
  const list = SUBS.filter(s => {
    if (s.conference_id && !venueOK(s.venue)) return false;
    if (!s.conference_id && F.venues.size) return false;
    if (F.starred && s.conference_id && !isStar("series", s.series_id)) return false;
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

/* ---------- admin: series → editions → milestones ---------- */
const DATE_FIELDS = [["abstract_date","Abstract"],["paper_date","Paper"],["notify_date","Notify"],["camera_date","Camera"],["start_date","Start"],["end_date","End"]];
const OPEN = new Set();   // admin panels left open across re-renders
function seriesFields(s) {
  return `<table class="edit sfields"><tbody>
    <tr><th>Acronym</th><td><input data-f="acronym" value="${esc(s.acronym)}"></td><th>Name</th><td><input data-f="name" value="${esc(s.name)}"></td></tr>
    <tr><th>Homepage</th><td colspan="3"><input data-f="homepage" value="${esc(s.homepage)}" placeholder="the series' own site, not one year's"></td></tr>
    <tr><th>Emphasis</th><td colspan="3"><input data-f="emphasis" value="${esc(s.emphasis)}"></td></tr>
    <tr><th>Fit</th><td colspan="3"><input data-f="fit" value="${esc(s.fit)}"></td></tr>
    <tr><th>Notes</th><td colspan="3"><input data-f="notes" value="${esc(s.notes)}"></td></tr>
  </tbody></table>`;
}
function editionPanel(c) {
  const key = "c:" + c.id;
  return `<details class="panel edpanel" style="margin:8px 0 8px 14px" data-cid="${esc(c.id)}" data-open="${key}" ${OPEN.has(key) ? "open" : ""}>
    <summary style="padding:9px 12px;cursor:pointer;font-weight:600">${esc(c.label)} <span style="font-weight:400;color:var(--text-3)">— ${esc(c.location || "location TBA")}</span></summary>
    <div style="padding:4px 12px 12px">
      <table class="edit efields"><tbody>
        <tr><th>Year</th><td><input type="number" data-f="year" value="${c.year}"></td><th>Status</th><td><input data-f="status" value="${esc(c.status)}"></td></tr>
        <tr><th>Location</th><td><input data-f="location" value="${esc(c.location)}"></td><th>Map site key</th><td><input data-f="site_key" value="${esc(c.site_key || "")}" placeholder="e.g. Basel, Switzerland"></td></tr>
        <tr><th>URL</th><td><input data-f="url" value="${esc(c.url)}" placeholder="this year's site"></td><th>Source</th><td><input data-f="source" value="${esc(c.source)}"></td></tr>
        ${DATE_FIELDS.map(([f, l]) => `<tr><th>${l}</th><td colspan="3"><input type="date" data-f="${f}" value="${c[f] || ""}"></td></tr>`).join("")}
        <tr><th>Notes</th><td colspan="3"><input data-f="notes" value="${esc(c.notes)}"></td></tr>
      </tbody></table>
      <div class="rowbtns" style="margin:10px 0 16px">
        <button class="btn sm saveed">Save edition</button>
        <button class="btn danger sm deled">Delete edition</button>
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
    </div></details>`;
}
function renderAdmin() {
  if (!ME?.is_admin) { $("#adminlist").innerHTML = ""; return; }
  $("#adminlist").innerHTML = SERIES.map(s => {
    const key = "s:" + s.id;
    const eds = CONFS.filter(c => c.series_id === s.id).sort((a, b) => b.year - a.year);
    return `<details class="panel serpanel" style="margin-bottom:10px" data-sid="${esc(s.id)}" data-open="${key}" ${OPEN.has(key) ? "open" : ""}>
    <summary style="padding:11px 14px;cursor:pointer;font-weight:600">${esc(s.acronym)} <span style="font-weight:400;color:var(--text-3)">— ${esc(s.name)} · ${s.years.length ? s.years.join(", ") : "no editions"}</span></summary>
    <div style="padding:4px 14px 14px">
      ${seriesFields(s)}
      <div class="rowbtns" style="margin:10px 0 6px">
        <button class="btn sm saveser">Save series</button>
        <button class="btn ghost sm added">+ Add edition</button>
        <button class="btn ghost sm histser">History</button>
        <button class="btn danger sm delser">Delete series</button>
      </div>
      ${eds.map(editionPanel).join("")}
    </div></details>`;
  }).join("");
  wireAdmin();
}
function fieldsOf(scope) {
  const out = {};
  scope.querySelectorAll("[data-f]").forEach(i => { out[i.dataset.f] = i.value || (i.type === "date" ? null : ""); });
  return out;
}
async function adminDo(promise, ok) {
  try { await promise; await reload(); flash(ok); }
  catch (err) { flash("Failed: " + err.message); }
}
function wireAdmin() {
  $$("#adminlist details[data-open]").forEach(d => d.ontoggle = () => {
    d.open ? OPEN.add(d.dataset.open) : OPEN.delete(d.dataset.open);
  });
  $$("#adminlist .serpanel").forEach(det => {
    const sid = det.dataset.sid, s = SERIES_BY_ID.get(sid);
    det.querySelector(".saveser").onclick = () =>
      adminDo(api(`/api/series/${sid}`, { method: "PUT", body: fieldsOf(det.querySelector(".sfields")) }), "Series saved");
    det.querySelector(".histser").onclick = () => { $("#chseries").value = sid; showPane("changes"); };
    det.querySelector(".delser").onclick = () => {
      if (!window.confirm(`Delete the series ${s.acronym}? Only possible once it has no editions.`)) return;
      adminDo(api(`/api/series/${sid}`, { method: "DELETE" }), "Series deleted");
    };
    det.querySelector(".added").onclick = () => {
      const next = s.years.length ? Math.max(...s.years) + 1 : today.getFullYear() + 1;
      const year = window.prompt(`Year of the new ${s.acronym} edition`, String(next));
      if (!year) return;
      OPEN.add("s:" + sid);
      adminDo(api(`/api/series/${sid}/editions`, { method: "POST", body: { year: +year } }), "Edition added");
    };
  });
  $$("#adminlist .edpanel").forEach(det => {
    const cid = det.dataset.cid;
    det.querySelector(".saveed").onclick = () =>
      adminDo(api(`/api/conferences/${cid}`, { method: "PUT", body: fieldsOf(det.querySelector(".efields")) }), "Edition saved");
    det.querySelector(".deled").onclick = () => {
      if (!window.confirm("Delete this edition and all its milestones? Ideas registered for it keep their text but lose the venue.")) return;
      adminDo(api(`/api/conferences/${cid}`, { method: "DELETE" }), "Edition deleted");
    };
    det.querySelectorAll("tr[data-mid]").forEach(tr => {
      tr.querySelector(".savems").onclick = () =>
        adminDo(api(`/api/milestones/${tr.dataset.mid}`, { method: "PUT", body: fieldsOf(tr) }), "Milestone saved");
      tr.querySelector(".delms").onclick = () =>
        adminDo(api(`/api/milestones/${tr.dataset.mid}`, { method: "DELETE" }), "Milestone deleted");
    });
    const newRow = det.querySelector("tr[data-new]");
    newRow.querySelector(".addms").onclick = () => {
      const body = fieldsOf(newRow);
      if (!body.date) { flash("A date is required"); return; }
      adminDo(api(`/api/conferences/${cid}/milestones`, { method: "POST", body }), "Milestone added");
    };
  });
}

/* ---------- admin: change log (with undo) and API tokens ---------- */
let CHANGES = [], CH_MORE = false;
const CH_PAGE = 50;
const ACTION = { create: "added", update: "changed", delete: "deleted" };
const dstr = iso => { const d = new Date(iso); return `${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`; };
const tstr = iso => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const shortVal = v => {
  if (v === null || v === undefined || v === "") return "∅";
  const t = String(v); return t.length > 90 ? t.slice(0, 87) + "…" : t;
};
const safeUrl = u => /^https?:\/\//i.test(u || "");
function changeDiff(c) {
  if (c.action === "update")
    return c.fields.map(f => `${esc(f)}: <del>${esc(shortVal(c.before[f]))}</del> → <ins>${esc(shortVal(c.after[f]))}</ins>`).join("<br>");
  const snap = c.after || c.before || {};
  if (c.entity === "milestone") return esc(snap.end_date ? `${snap.date} – ${snap.end_date}` : snap.date);
  if (c.entity === "edition") {
    const n = (snap.milestones || []).length;
    return n ? `with ${n} milestone${n > 1 ? "s" : ""}` : "";
  }
  return "";
}
function changeRow(c) {
  const via = c.via === "token" ? `API token “${esc(c.token)}”` : "the app";
  const src = !c.source ? "" : safeUrl(c.source)
    ? ` · <a href="${esc(c.source)}" target="_blank" rel="noopener noreferrer">source</a>`
    : ` · source: ${esc(c.source)}`;
  const tags = (c.undo_of ? `<span class="tag">undo of #${c.undo_of}</span>` : "")
    + (c.undone_by ? `<span class="tag">undone by #${c.undone_by}</span>` : "");
  const diff = changeDiff(c);
  return `<div class="chrow ${c.undone_by ? "undone" : ""}" data-ch="${c.id}">
    <div class="when">${dstr(c.at)}<br>${tstr(c.at)}</div>
    <div><div class="what"><span class="act">${ACTION[c.action] || esc(c.action)} ${esc(c.entity)}</span><b>${esc(c.label)}</b>${tags}</div>
      ${diff ? `<div class="diff">${diff}</div>` : ""}
      <div class="who">#${c.id} · ${esc(c.actor)} via ${via}${src}${c.note ? " · " + esc(c.note) : ""}</div></div>
    <div>${c.undone_by ? "" : '<button class="btn ghost sm chundo">Undo</button>'}</div></div>`;
}
async function loadChanges(more) {
  const params = new URLSearchParams({ limit: String(CH_PAGE) });
  if ($("#chseries").value) params.set("series", $("#chseries").value);
  if (more && CHANGES.length) params.set("before", String(CHANGES[CHANGES.length - 1].id));
  const page = await api("/api/changes?" + params);
  CHANGES = more ? CHANGES.concat(page) : page;
  CH_MORE = page.length === CH_PAGE;
  renderChanges();
}
function renderChanges() {
  $("#chlist").innerHTML = CHANGES.length ? CHANGES.map(changeRow).join("")
    : '<div class="chempty">No changes recorded yet.</div>';
  $("#chmore").classList.toggle("hidden", !CH_MORE);
  $$("#chlist .chundo").forEach(b => b.onclick = async () => {
    const id = b.closest("[data-ch]").dataset.ch;
    try {
      await api(`/api/changes/${id}/undo`, { method: "POST" });
      await reload(); await loadChanges(false); flash("Undone");
    } catch (err) { flash("Could not undo: " + err.message); }
  });
}
function fillChangeSeries() {
  const keep = $("#chseries").value;
  $("#chseries").innerHTML = '<option value="">All series</option>'
    + SERIES.map(s => `<option value="${esc(s.id)}">${esc(s.acronym)}</option>`).join("");
  $("#chseries").value = SERIES_BY_ID.has(keep) ? keep : "";
}
async function loadTokens() {
  const list = await api("/api/tokens");
  const now = Date.now();
  $("#tklist").innerHTML = !list.length ? '<div class="chempty">No tokens yet.</div>'
    : `<table class="tktable"><thead><tr><th>Name</th><th>Owner</th><th class="opt">Token</th><th class="opt">Created</th><th>Expires</th><th class="opt">Last used</th><th></th></tr></thead><tbody>
      ${list.map(t => {
        const off = t.revoked_at ? "revoked" : (new Date(t.expires_at).getTime() < now ? "expired" : "");
        return `<tr class="${off ? "off" : ""}" data-tk="${esc(t.id)}"><td>${esc(t.name)}</td><td>${esc(t.owner)}</td>
          <td class="opt"><code>${esc(t.prefix)}…</code></td><td class="opt">${dstr(t.created_at)}</td>
          <td>${off || dstr(t.expires_at)}</td><td class="opt">${t.last_used_at ? dstr(t.last_used_at) : "never"}</td>
          <td>${off ? "" : '<button class="btn danger sm tkrevoke">Revoke</button>'}</td></tr>`;
      }).join("")}</tbody></table>`;
  $$("#tklist .tkrevoke").forEach(b => b.onclick = async () => {
    if (!window.confirm("Revoke this token? Anything using it stops working immediately.")) return;
    try { await api(`/api/tokens/${b.closest("[data-tk]").dataset.tk}`, { method: "DELETE" }); await loadTokens(); flash("Token revoked"); }
    catch (err) { flash("Could not revoke: " + err.message); }
  });
}
function showPane(p) {
  $$("[data-a]").forEach(x => x.classList.toggle("on", x.dataset.a === p));
  $$(".apane").forEach(x => x.classList.toggle("hidden", x.id !== "a-" + p));
  const fail = err => flash("Could not load: " + err.message);
  if (p === "changes") loadChanges(false).catch(fail);
  if (p === "tokens") loadTokens().catch(fail);
}

/* ---------- render ---------- */
function renderAll() {
  renderFilterBar(); renderDeadlines(); renderConfs();
  renderTimeline(); renderMap(); renderSubs(); renderAdmin();
}

/* ---------- bootstrap ---------- */
function indexData(data) {
  SERIES = data.series;
  SERIES_BY_ID = new Map(SERIES.map(x => [x.id, x]));
  CONFS = data.conferences;
  CONF_BY_ID = new Map(CONFS.map(c => [c.id, c]));
  for (const [sid, cid] of SEL) if (!CONF_BY_ID.has(cid)) SEL.delete(sid);
  MSR = data.milestones.map(m => ({ ...m, _k: kindOf(m) }))
    .sort((a, b) => a.date.localeCompare(b.date));
  SUBS = data.submissions;
  STARS = { series: new Set(data.stars.series || []), milestone: new Set(data.stars.milestone || []) };
  ME = data.me;
}
async function reload() { indexData(await api("/api/bootstrap")); fillVenuePicker(); fillChangeSeries(); renderAll(); }
function fillVenuePicker() {
  const keep = $("#f-venue").value;
  const eds = CONFS.slice().sort((a, b) => a.venue.localeCompare(b.venue) || b.year - a.year);
  $("#f-venue").innerHTML = '<option value="">— pick a venue —</option>'
    + eds.map(c => `<option value="${esc(c.id)}">${esc(c.label)} — ${esc(c.name)}</option>`).join("");
  $("#f-venue").value = keep;
}

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

  fillVenuePicker(); fillChangeSeries();
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
  const acronym = (window.prompt("Acronym of the new series (e.g. ESWC)") || "").trim();
  if (!acronym) return;
  try {
    const s = await api("/api/series", { method: "POST", body: { acronym, name: acronym } });
    OPEN.add("s:" + s.id); await reload(); flash("Series created — add its first edition");
  } catch (err) { flash("Could not create: " + err.message); }
};

$$("[data-a]").forEach(b => b.onclick = () => showPane(b.dataset.a));
$("#chseries").onchange = () => loadChanges(false).catch(err => flash("Could not load: " + err.message));
$("#chmore").onclick = () => loadChanges(true).catch(err => flash("Could not load: " + err.message));
$("#tkcreate").onclick = async () => {
  const name = $("#tkname").value.trim();
  if (!name) { flash("Say what the token is for"); return; }
  try {
    const t = await api("/api/tokens", { method: "POST", body: { name, expires_in_days: +$("#tkdays").value } });
    $("#tkvalue").textContent = t.token; $("#tknew").classList.remove("hidden");
    $("#tkmcp").textContent = `claude mcp add --transport http conference-tracker ${location.origin}/mcp --header "Authorization: Bearer ${t.token}"`;
    $("#tkname").value = ""; await loadTokens();
  } catch (err) { flash("Could not create: " + err.message); }
};
$("#tkcopy").onclick = () => copyText($("#tkvalue").textContent);
$("#tkmcpcopy").onclick = () => copyText($("#tkmcp").textContent);

start();
