"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
const time = (iso) => new Date(iso).toLocaleTimeString();
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
let state = null;
let profileDirty = false;

const STATUS = {
  unregistered: ["Not registered", "This worker has not asked the server to join yet."],
  waiting: ["Waiting for approval", "Registration sent. The server has to approve it and allocate an enrolled ID."],
  no_channel: ["Enrolled — no channel", "The server approved this worker, but no channel is allocated yet."],
  active: ["Working", "Enrolled and connected to a channel. Listening for messages."],
  declined: ["Registration declined", "The server refused the request, or it expired. You can register again."],
};

// --- tabs ---
document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
  history.replaceState(null, "", `#${name}`);
}
showTab(["workspace", "report", "profile"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "workspace");

// --- collapsible panels ---
// Each .card.collapsible keeps its header row; everything after it folds away.
// The chevron points up (shrink) when open and turns down (expand) when closed.
const CHEVRON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>';
const COLLAPSE_KEY = "repo-finder.collapsed";
const collapsedSet = (() => { try { return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]")); } catch { return new Set(); } })();

document.querySelectorAll(".card.collapsible").forEach((card) => {
  const head = card.querySelector(":scope > .status-head, :scope > .card-head");
  const inner = el("div", "collapse-inner");
  const body = el("div", "collapse-body");
  while (head.nextSibling) inner.append(head.nextSibling);
  body.append(inner);
  card.append(body);

  const btn = el("button", "toggle");
  btn.type = "button";
  btn.innerHTML = CHEVRON;
  head.querySelector(".head-tools").append(btn);
  head.classList.add("collapse-head");

  const apply = (collapsed) => {
    card.classList.toggle("collapsed", collapsed);
    body.inert = collapsed;
    btn.setAttribute("aria-expanded", String(!collapsed));
    btn.title = btn.ariaLabel = collapsed ? "Expand panel" : "Shrink panel";
  };
  const toggle = () => {
    const collapsed = !card.classList.contains("collapsed");
    apply(collapsed);
    collapsed ? collapsedSet.add(card.id) : collapsedSet.delete(card.id);
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsedSet])); } catch { /* storage off */ }
  };
  btn.addEventListener("click", (e) => { e.stopPropagation(); toggle(); });
  head.addEventListener("click", (e) => { if (!e.target.closest("button, a, input")) toggle(); });
  apply(collapsedSet.has(card.id));
});

// --- api ---
async function api(path, body) {
  const res = await fetch(path, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(text) {
  const t = $("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2800);
}

// --- register flow ---
$("register-btn").addEventListener("click", () => {
  $("confirm-url").textContent = state.server_url;
  $("confirm-name").textContent = state.name;
  $("confirm-dialog").showModal();
});
$("confirm-dialog").addEventListener("close", async () => {
  if ($("confirm-dialog").returnValue !== "register") return;
  $("register-btn").disabled = true;
  try {
    render(await api("/api/register", {}));
    toast("Registration sent — waiting for approval");
  } catch (err) {
    toast(err.message);
  } finally {
    $("register-btn").disabled = false;
  }
});
$("forget-btn").addEventListener("click", async () => {
  if ($("forget-btn").dataset.armed !== "1") {
    $("forget-btn").dataset.armed = "1";
    $("forget-btn").textContent = "Click again to forget";
    setTimeout(() => { $("forget-btn").dataset.armed = ""; $("forget-btn").textContent = "Forget registration"; }, 3000);
    return;
  }
  render(await api("/api/cancel", {}));
  toast("Registration forgotten");
});
// --- members ---
const memberForm = $("member-form");
const memberError = (text) => { $("member-error").textContent = text || ""; $("member-error").hidden = !text; };
let editingMember = null;
function openMemberDialog(member) {
  editingMember = member || null;
  memberForm.reset();
  memberError("");
  $("member-dialog-title").textContent = member ? `Edit ${member.name}` : "Add member";
  $("member-submit").textContent = member ? "Save" : "Add";
  if (member) {
    memberForm.name.value = member.name;
    memberForm.from.value = member.from;
    memberForm.to.value = member.to || "";
    memberForm.present.checked = !member.to;
  }
  const sim = member?.similarity ?? 0.6;
  memberForm.similarity.value = memberForm.similarity_range.value = sim;
  memberForm.to.disabled = memberForm.present.checked;
  $("member-dialog").showModal();
  (member && member.similarity == null ? memberForm.similarity : memberForm.name).focus();
}
$("add-member-btn").addEventListener("click", (e) => { e.stopPropagation(); openMemberDialog(); });
memberForm.similarity_range.addEventListener("input", () => { memberForm.similarity.value = memberForm.similarity_range.value; });
memberForm.similarity.addEventListener("input", () => { memberForm.similarity_range.value = memberForm.similarity.value; });
$("member-cancel").addEventListener("click", () => $("member-dialog").close());
memberForm.present.addEventListener("change", () => {
  memberForm.to.disabled = memberForm.present.checked;
  if (memberForm.present.checked) memberForm.to.value = "";
});
memberForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = memberForm.name.value.trim();
  const from = memberForm.from.value;
  const to = memberForm.present.checked ? "" : memberForm.to.value;
  if (!name) return memberError("Enter a name.");
  if (!from) return memberError("Pick the month they started (From).");
  if (!to && !memberForm.present.checked) return memberError("Pick an end month (To), or tick “Still a member”.");
  if (to && to < from) return memberError("“To” can't be before “From”.");
  const similarity = Number(memberForm.similarity.value);
  if (memberForm.similarity.value === "" || !(similarity >= 0 && similarity <= 1)) return memberError("Similarity must be from 0 to 1.");
  try {
    if (editingMember) {
      render(await api("/api/members/update", { id: editingMember.id, name, from, to, similarity }));
      toast(`Saved ${name}`);
    } else {
      render(await api("/api/members", { name, from, to, similarity }));
      toast(`Added ${name}`);
    }
    $("member-dialog").close();
  } catch (err) { memberError(err.message); }
});

const monthName = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
};
function span(from, to) {
  const [fy, fm] = from.split("-").map(Number);
  const end = to ? to.split("-").map(Number) : [new Date().getFullYear(), new Date().getMonth() + 1];
  const months = (end[0] - fy) * 12 + (end[1] - fm) + 1;   // inclusive of both months
  const y = Math.floor(months / 12), mo = months % 12;
  return [y && `${y} yr${y > 1 ? "s" : ""}`, mo && `${mo} mo${mo > 1 ? "s" : ""}`].filter(Boolean).join(" ") || "1 mo";
}
const pendingRemove = new Set();
function renderMembers(members) {
  const box = $("members-list");
  $("sum-members").textContent = members.length ? `${members.length} member${members.length > 1 ? "s" : ""}` : "No members yet";
  if (!members.length) {
    box.replaceChildren(el("p", "empty", "No members yet. Use “Add member” to register one."));
    return;
  }
  const sorted = members.slice().sort((a, b) => (b.to ? 0 : 1) - (a.to ? 0 : 1) || b.from.localeCompare(a.from));
  box.replaceChildren(...sorted.map((m) => {
    const row = el("div", "member" + (m.to ? "" : " current"));
    row.title = "Click to edit";
    row.addEventListener("click", (e) => { if (!e.target.closest("button")) openMemberDialog(m); });
    const who = el("div", "member-main");
    const name = el("div", "member-name");
    name.append(el("span", "avatar", m.name.trim().charAt(0).toUpperCase()), el("span", null, m.name));
    if (!m.to) name.append(el("span", "tag ok", "Current"));
    who.append(name);
    const dates = el("div", "member-dates");
    dates.append(el("span", "range", `${monthName(m.from)} – ${m.to ? monthName(m.to) : "Present"}`), el("span", "muted", span(m.from, m.to)));
    dates.append(m.similarity == null
      ? el("span", "tag warn", "Similarity not set — jobs wait")
      : el("span", "tag sim", `Similarity ≥ ${Number(m.similarity).toFixed(2)}`));
    who.append(dates);
    const editBtn = el("button", "btn small ghost edit", "✎ Edit");
    editBtn.type = "button";
    editBtn.addEventListener("click", () => openMemberDialog(m));
    const del = el("button", "btn small ghost remove", pendingRemove.has(m.id) ? "Confirm remove" : "Remove");
    del.type = "button";
    if (pendingRemove.has(m.id)) del.classList.add("armed");
    del.addEventListener("click", async () => {
      if (!pendingRemove.has(m.id)) {
        pendingRemove.add(m.id);
        renderMembers(state.members);
        setTimeout(() => { pendingRemove.delete(m.id); renderMembers(state.members); }, 3000);
        return;
      }
      pendingRemove.delete(m.id);
      render(await api("/api/members/delete", { id: m.id }));
      toast(`Removed ${m.name}`);
    });
    const tools = el("div", "member-tools");
    tools.append(editBtn, del);
    row.append(who, tools);
    return row;
  }));
}

$("clear-btn").addEventListener("click", async () => render(await api("/api/clear-log", {})));

// --- profile ---
const form = $("profile-form");
form.addEventListener("input", () => { profileDirty = true; });
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  try {
    render(await api("/api/profile", data));
    profileDirty = false;
    $("saved-note").textContent = "Saved";
    setTimeout(() => { $("saved-note").textContent = ""; }, 2000);
  } catch (err) { toast(err.message); }
});

// --- render ---

function render(s) {
  state = s;
  const st = s.status;
  const [title, text] = STATUS[st] || [st, ""];
  $("debug-badge").hidden = !s.debug;
  $("debug-card").hidden = !s.debug;

  // workspace
  $("status-title").textContent = title;
  $("status-text").textContent = text;
  $("status-pill").textContent = st.replace("_", " ");
  $("status-pill").className = `pill ${st}`;
  $("channel-warning").hidden = st !== "no_channel";
  $("error-box").hidden = !s.last_error;
  $("error-box").textContent = s.last_error ? `Can't reach the server (${time(s.last_error.at)}): ${s.last_error.text}. Retrying…` : "";

  const reached = { request: !!(s.requested_at && st !== "unregistered" && st !== "declined") || !!s.worker_id,
    approved: !!s.worker_id, channel: st === "active" };
  const order = ["request", "approved", "channel"];
  const current = order.find((k) => !reached[k]);
  document.querySelectorAll("#steps li").forEach((li) => {
    const k = li.dataset.step;
    li.className = reached[k] ? "done" : (k === current && st !== "unregistered" && st !== "declined" ? "current" : "");
  });

  $("f-server").textContent = s.server_url;
  $("f-id").textContent = s.worker_id || "not allocated";
  $("f-channels").textContent = s.channels.length ? s.channels.join(", ") : (st === "active" ? "allocated" : "none");
  $("f-contact").textContent = fmt(s.last_contact);

  const registered = ["waiting", "no_channel", "active"].includes(st);
  $("register-btn").hidden = registered;
  $("forget-btn").hidden = !registered;

  // jobs
  const box = $("messages");
  $("msg-count").textContent = s.messages.length ? `${s.messages.length} received` : "";
  if (!s.messages.length) {
    box.replaceChildren(el("p", "empty", "No jobs yet. They appear here once the server sends them."));
  } else {
    box.replaceChildren(...s.messages.slice().reverse().map(renderJob));
  }

  // report
  $("s-pings").textContent = s.stats.pings;
  $("s-polls").textContent = s.stats.polls;
  $("s-messages").textContent = s.stats.messages;
  $("s-errors").textContent = s.stats.errors;
  $("log").replaceChildren(...(s.log.length ? s.log.slice().reverse().map((l) => {
    const row = el("div", "log-row");
    row.append(el("span", "muted", time(l.at)), el("span", `lvl ${l.level}`, l.level), el("span", null, l.text));
    return row;
  }) : [el("p", "empty", "No activity yet.")]));
  if (s.debug) $("last-response").textContent = s.last_response ? JSON.stringify(s.last_response, null, 2) : "—";

  // profile
  if (!profileDirty) {
    form.name.value = s.name; form.label.value = s.label; form.server_url.value = s.server_url;
  }
  form.name.disabled = registered; form.server_url.disabled = registered;
  $("profile-lock").hidden = !registered;
  $("p-status").textContent = title;
  $("p-id").textContent = s.worker_id || "not allocated";
  $("p-requested").textContent = fmt(s.requested_at);
  $("p-enrolled").textContent = fmt(s.enrolled_at);
  $("p-channel").textContent = fmt(s.channel_at);
  $("p-host").textContent = s.host;
  $("sum-profile").textContent = `${s.name} · ${s.server_url}`;
  renderMembers(s.members || []);
  $("sum-identity").textContent = s.worker_id ? `ID ${s.worker_id}` : "No enrolled ID yet";
}

const openRows = new Set();   // expanded non-job rows survive the 1.5 s re-render

// A job arrives as {channel, seq, sender, body: {job, title, person, description}}.
function renderJob(m) {
  const b = m.body && typeof m.body === "object" ? m.body : {};
  const title = b.title || m.fields?.title;
  if (!title) {   // not a job: show it raw
    const row = el("details", "msg");
    const what = [b.event && `event: ${b.event}`, b.job_id && `job ${b.job_id}`, b.worker && `by ${b.worker}`].filter(Boolean).join(" · ");
    row.append(el("summary", "msg-meta", `${what || "message"} — #${m.channel || ""} · seq ${m.seq ?? "—"} · ${time(m.received_at)}`));
    row.append(el("pre", null, typeof m.body === "string" ? m.body : JSON.stringify(m.body ?? m, null, 2)));
    const key = `${m.channel}:${m.seq}`;
    row.open = openRows.has(key);
    row.addEventListener("toggle", () => { row.open ? openRows.add(key) : openRows.delete(key); });
    return row;
  }
  const card = el("article", "job");
  const main = el("div", "job-main");
  main.append(el("h4", "job-title", title));
  const meta = el("div", "job-meta");
  const person = el("span", "job-person");
  person.append(el("span", "avatar", (b.person || "?").trim().charAt(0).toUpperCase()), el("span", null, b.person || "Unknown"));
  meta.append(person, el("span", "job-id", `#${b.job || m.seq}`));
  main.append(meta);
  main.append(el("div", "job-foot", `${m.channel ? "#" + m.channel : ""}${m.sender ? " · from " + m.sender : ""} · ${fmt(m.received_at)}`));
  const btn = el("button", "btn small primary", "View JD");
  btn.type = "button";
  btn.addEventListener("click", () => openJD(m, b, title));
  const top = el("div", "job-top");
  top.append(main, btn);
  card.append(top);
  const match = b.job && state.matches?.[b.job];
  if (match) card.append(renderMatch(match));
  if (match && match.status !== "unrelated") {
    card.classList.add("related");
    meta.append(el("span", "tag related-tag", `Related · ${match.member}`));
  }
  return card;
}

const period = (x) => x.period ? `${monthName(x.period[0])} – ${x.period[1] ? monthName(x.period[1]) : "now"}` : "the registered period";

// What the finder made of a job: shown under the card, and the links it sent.
function renderMatch(x) {
  const box = el("div", `match ${x.status}`);
  const head = el("div", "match-head");
  const say = {
    unrelated: `Not related — ${x.reason}`,
    needs_threshold: `Related to ${x.member}. Set ${x.member}'s similarity in Profile → Registered members to start the search.`,
    queued: "Queued for a GitHub search…",
    searching: `Searching GitHub for repos with similarity ≥ ${Number(x.threshold).toFixed(2)}, made within ${period(x)}, and analysing them…`,
    found: `Similar repos (≥ ${Number(x.threshold).toFixed(2)}, made within ${period(x)})`,
    none: `No repo made within ${period(x)} reached ${Number(x.threshold).toFixed(2)} — the best of ${x.considered} scored ${Number(x.best_score).toFixed(2)}`,
    error: `Search failed: ${x.error}`,
  }[x.status] || x.status;
  head.append(el("span", "match-title", say));
  if (x.status === "unrelated") { box.append(head); return box; }
  if (!["queued", "searching", "needs_threshold"].includes(x.status)) {
    const again = el("button", "btn small ghost", "Search again");
    again.type = "button";
    again.addEventListener("click", async () => {
      again.disabled = true;
      try { render(await api("/api/jobs/search", { job_id: x.job_id })); toast("Searching again"); }
      catch (err) { toast(err.message); again.disabled = false; }
    });
    head.append(again);
  }
  box.append(head);
  if (x.status === "found") {
    const list = el("div", "repos");
    for (const r of x.repos) {
      const row = el("div", "repo");
      const topRow = el("div", "repo-top");
      const left = el("div", "repo-main");
      const link = el("a", "repo-name", r.name);
      link.href = r.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      left.append(link);
      if (r.description) left.append(el("div", "repo-desc", r.description));
      const facts = el("div", "repo-facts");
      if (r.created) facts.append(el("span", null, `${r.created} → ${r.last_push}`));
      if (r.language) facts.append(el("span", null, r.language));
      for (const [k, v] of Object.entries(r.parts || {})) facts.append(el("span", null, `${k} ${Math.round(v * 100)}%`));
      left.append(facts);
      const score = el("div", "score");
      score.append(el("div", "score-v", r.score.toFixed(2)));
      const bar = el("div", "score-bar"); const fill = el("div"); fill.style.width = `${Math.round(r.score * 100)}%`;
      bar.append(fill); score.append(bar);
      topRow.append(left, score);
      row.append(topRow);
      if (r.analysis) row.append(renderAnalysis(r.analysis, `${x.job_id}:${r.url}`));
      list.append(row);
    }
    box.append(list);
    const rep = x.reply || {};
    const replyText = { sent: `✓ Sent to #${x.channel} as a reply to job ${x.job_id}`,
      failed: `✗ Server refused the reply: ${rep.error}`, pending: `Reply waiting: ${rep.error}` }[rep.status] || "Sending reply…";
    box.append(el("div", `reply ${rep.status || "sending"}`, replyText));
  }
  return box;
}

const ANALYSIS = [["requirements", "Project requirements"], ["workflow", "Workflow"], ["tech_stack", "Tech stack"],
  ["architecture", "Architecture"], ["know_how", "Know-how"], ["smart_approach", "Smart approach"]];
function renderAnalysis(a, key) {
  const d = el("details", "analysis");
  d.open = openRows.has(key);
  d.addEventListener("toggle", () => { d.open ? openRows.add(key) : openRows.delete(key); });
  const n = ANALYSIS.reduce((sum, [k]) => sum + (a[k]?.length || 0), 0);
  d.append(el("summary", null, `Analysis · ${n} findings`));
  const grid = el("div", "analysis-grid");
  for (const [k, label] of ANALYSIS) {
    if (!a[k]?.length) continue;
    const sec = el("section", `an ${k}`);
    sec.append(el("h5", null, label));
    const ul = el("ul");
    a[k].forEach((item) => ul.append(el("li", null, item)));
    sec.append(ul);
    grid.append(sec);
  }
  d.append(grid);
  return d;
}

function openJD(m, b, title) {
  $("jd-meta").textContent = `Job ${b.job || "—"} · #${m.channel || ""} · seq ${m.seq ?? "—"}`;
  $("jd-title").textContent = title;
  $("jd-person").textContent = b.person ? `Posted by ${b.person}` : "";
  $("jd-body").textContent = b.description || m.fields?.description || "No description was sent with this job.";
  $("jd-dialog").showModal();
}

async function refresh() {
  try { render(await api("/api/state")); } catch { /* server restarting in debug mode */ }
}
refresh();
setInterval(refresh, 1500);
