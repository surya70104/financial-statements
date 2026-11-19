/* Shared shell: engagement label, user/role switcher, Lakhs/Rupees toggle, API helper, DOM helper. */
const App = (() => {
  const state = { engagement: null, users: [], user: null, unit: "LAKH" };
  const listeners = [];

  function store(key, value) {                       // localStorage can be unavailable: never let it break the page
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    return null;
  }

  function h(tag, attrs, ...kids) {                  // safe element builder (text is never parsed as HTML)
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers);
    if (state.user) headers["X-User-Id"] = String(state.user.id);
    let body = opts.body;
    if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
    const res = await fetch(path, { method: opts.method || "GET", headers, body });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error(typeof d === "string" ? d : (d && d.message) || (Array.isArray(d) ? d.map(x => x.msg).join("; ") : "Request failed (" + res.status + ")"));
      err.status = res.status; err.detail = d;
      throw err;
    }
    if ((opts.method || "GET") !== "GET") refreshChecksBadge();
    return data;
  }

  let toastTimer;
  function toast(message, bad) {
    const t = document.getElementById("toast");
    t.textContent = message; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 7000 : 3500);
  }

  const money = v => Money.format(v, state.unit);
  const unitLabel = () => (state.unit === "LAKH" ? "₹ in lakh" : "₹");
  const isLocked = () => !!(state.engagement && state.engagement.is_locked);
  const canEdit = () => !isLocked() && !!state.user && state.user.roles.some(r => r === "MAKER" || r === "PARTNER");
  const onChange = fn => listeners.push(fn);
  const notify = what => listeners.forEach(fn => fn(what));

  function renderUnitToggle() {
    document.querySelectorAll("#unit-toggle button").forEach(b => b.classList.toggle("on", b.dataset.unit === state.unit));
  }

  function renderHeader() {
    const e = state.engagement;
    document.getElementById("engagement-label").textContent =
      e.client_name + " | FY " + e.fy_label + (e.engagement_type === "STATUTORY_AUDIT" ? " | Statutory Audit" : "");
    document.getElementById("template-banner").hidden = !!e.template_confirmed_at;
    document.getElementById("template-name").textContent = e.template_name;
    setUnmappedBadge(e.counts.unmapped_ledgers);
    setChecksBadge(e.checks);
    setIssuesBadge(e.issues);
    document.getElementById("locked-badge").hidden = !e.is_locked;
    renderMaterialityBadge();
  }

  function renderMaterialityBadge() {
    const b = document.getElementById("materiality-badge"), m = state.engagement && state.engagement.materiality;
    if (!b) return;
    b.hidden = !m;
    if (m) {
      b.textContent = "Materiality: " + money(m.overall_materiality) + (state.unit === "LAKH" ? " lakh" : "") + " (set by " + m.set_by + ")";
      b.title = "Overall materiality " + Money.exact(m.overall_materiality) + " set " + new Date(m.set_at).toLocaleString();
    }
  }

  function setIssuesBadge(c) {
    const b = document.getElementById("nav-issues");
    if (!b || !c) return;
    b.hidden = false; b.textContent = c.open; b.classList.toggle("zero", c.open === 0);
    b.title = c.open + " open issue(s)";
  }

  function setChecksBadge(c) {
    const b = document.getElementById("nav-checks");
    if (!b || !c) return;
    b.hidden = false;
    b.textContent = c.has_run ? c.passed + "/" + c.total : "–";
    b.classList.toggle("zero", !!c.has_run && c.passed === c.total);
    b.title = c.has_run ? c.passed + " of " + c.total + " active checks passing" + (c.stale ? " (out of date: re-run)" : "") : "Checks not run yet";
  }

  // ---- print (T-15): the statutory report header is filled just before the browser prints ----
  const PRINT_TITLES = { bs: "Balance Sheet", pnl: "Statement of Profit and Loss", notes: "Notes to the Financial Statements",
                         ratios: "Ratios", pack: "Audit Committee / IA Pack", audit: "Audit Trail" };
  const ENTITY = { PRIVATE_LIMITED: "Private Limited Company", PUBLIC_LIMITED: "Public Limited Company", LLP: "Limited Liability Partnership",
                   PARTNERSHIP: "Partnership Firm", PROPRIETORSHIP: "Proprietorship" };
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  function longDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? Number(m[3]) + " " + MONTHS[Number(m[2]) - 1] + " " + m[1] : "";
  }
  function fillPrintHeader() {
    const e = state.engagement, set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
    if (!e) return;
    const page = document.body.dataset.page;
    set("ph-company", e.client_name);
    set("ph-entity", [e.cin ? "CIN: " + e.cin : "", e.pan ? "PAN: " + e.pan : "", ENTITY[e.entity_type] || ""].filter(Boolean).join("  ·  "));
    set("ph-title", PRINT_TITLES[page] || document.title.replace(" - CA Workbench", ""));
    set("ph-period", "Financial Year " + e.fy_label + "  ·  " + (page === "bs" ? "As at " : "For the year ended ") + longDate(e.period_end));
    set("ph-unit", state.unit === "LAKH" ? "Amount in ₹ Lakhs" : "Amount in ₹");
  }
  window.addEventListener("beforeprint", () => {
    fillPrintHeader();
    document.querySelectorAll("details").forEach(d => { d.dataset.wasOpen = d.open ? "1" : "0"; d.open = true; });   // print every note in full
  });
  window.addEventListener("afterprint", () => document.querySelectorAll("details").forEach(d => { if (d.dataset.wasOpen === "0") d.open = false; }));

  let badgeTimer;
  function refreshChecksBadge() {                      // any change (import, mapping, ...) re-runs checks server-side; mirror it in the tab
    clearTimeout(badgeTimer);
    badgeTimer = setTimeout(async () => {
      try { state.engagement = await api("/api/engagements/" + state.engagement.id); renderHeader(); } catch (e) { /* badges are best-effort */ }
    }, 250);
  }

  function setUnmappedBadge(n) {
    const b = document.getElementById("nav-unmapped");
    b.hidden = false; b.textContent = n; b.classList.toggle("zero", n === 0);
  }

  async function refreshHeader() {
    state.engagement = await api("/api/engagements/" + state.engagement.id);
    renderHeader();
  }

  async function init() {
    state.users = await api("/api/users");
    const savedUser = Number(store("cw.user"));
    state.user = state.users.find(u => u.id === savedUser) || state.users.find(u => u.roles.includes("MAKER")) || state.users[0];
    const sel = document.getElementById("user-switcher");
    state.users.forEach(u => sel.append(h("option", { value: u.id }, u.full_name + " [" + u.roles.join(", ") + "]")));
    sel.value = state.user.id;
    sel.addEventListener("change", () => {
      state.user = state.users.find(u => u.id === Number(sel.value));
      store("cw.user", state.user.id); notify("user");
    });

    const wanted = new URLSearchParams(location.search).get("engagement");
    const list = await api("/api/engagements");
    if (!list.length) throw new Error("No engagement found. Run the database initializer.");
    const id = wanted ? Number(wanted) : list[0].id;
    state.engagement = await api("/api/engagements/" + id);
    state.unit = store("cw.unit") || state.engagement.default_unit || "LAKH";
    renderHeader(); renderUnitToggle();
    document.querySelectorAll("[data-print]").forEach(b => b.addEventListener("click", () => window.print()));
    document.querySelectorAll("#unit-toggle button").forEach(b => b.addEventListener("click", () => {
      state.unit = b.dataset.unit; store("cw.unit", state.unit); renderUnitToggle(); renderMaterialityBadge(); notify("unit");
    }));
    return state;
  }

  const ready = init().catch(err => {
    document.getElementById("engagement-label").textContent = "Cannot load: " + err.message;
    toast(err.message, true); throw err;
  });

  return { state, ready, api, h, toast, money, unitLabel, canEdit, onChange, refreshHeader, setUnmappedBadge, setChecksBadge, setIssuesBadge, isLocked, fillPrintHeader };
})();
