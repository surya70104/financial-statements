/* Unified issue tracker (T-12): source filter, owner, approve / clear, group by cause. */
(function () {
  const { h } = App;
  let data = null;
  let clearing = null;                                // id of the manual issue whose clear form is open
  let approving = null;                               // id of the issue whose approve form is open
  const collapsed = new Set();                        // causes folded away in the grouped view
  const eid = () => App.state.engagement.id;
  const SOURCE = { check: ["bad", "CHECK"], mapping: ["warn", "MAPPING"], materiality: ["warn", "MATERIALITY"], gstr9c: ["bad", "GSTR-9C"], reconciliation: ["warn", "RECONCILIATION"],
                   udin: ["bad", "UDIN"], manual: ["ok", "MANUAL"] };
  const STATUS_CLASS = { open: "bad", approved: "warn", cleared: "ok" };
  const canApprove = () => !!App.state.user && App.state.user.roles.some(r => r === "CHECKER" || r === "PARTNER");
  const locked = () => !!App.state.engagement.is_locked;

  function filters() {
    return { source: document.getElementById("f-source").value, status: document.getElementById("f-status").value, group: document.getElementById("f-group").checked };
  }

  function renderSummary() {
    const s = data.summary;
    document.getElementById("issue-summary").replaceChildren(
      h("span", { class: "chip " + (s.open ? "bad" : "ok") }, s.open + " open"), h("span", { class: "chip ok" }, s.cleared + " cleared"),
      s.approved ? h("span", { class: "chip warn" }, s.approved + " approved") : null,
      ...["check", "mapping", "materiality", "gstr9c", "reconciliation", "udin", "manual"].map(k => h("span", { class: "muted" }, (SOURCE[k] ? SOURCE[k][1].toLowerCase() : k) + ": " + (s.by_source[k] ? s.by_source[k].open : 0) + " open")));
    App.setIssuesBadge(s);
  }

  function ownerCell(it) {
    if (it.status !== "open") return it.owner_name ? h("span", {}, it.owner_name) : h("span", { class: "muted" }, "—");
    const sel = h("select", { "aria-label": "Owner of issue " + it.id, disabled: locked(), onchange: e => assign(it, e.target.value) },
      h("option", { value: "" }, "Unassigned"),
      ...App.state.users.map(u => h("option", { value: u.id, selected: u.id === it.owner_id }, u.full_name.replace(/ \(.*\)$/, ""))));
    return sel;
  }

  function actionCell(it) {
    if (it.status !== "open") return h("span", { class: "muted" }, "");
    const parts = [];
    if (approving === it.id) {
      const note = h("input", { type: "text", placeholder: "Approval note (required)", maxlength: 500, "aria-label": "Approval note" });
      return h("div", { class: "clear-form" }, note, h("button", { type: "button", class: "primary", onclick: () => doApprove(it, note.value) }, "Approve issue"),
        h("button", { type: "button", onclick: () => { approving = null; render(); } }, "Cancel"));
    }
    if (clearing === it.id) {
      const note = h("input", { type: "text", placeholder: "Resolution note (required)", maxlength: 500, "aria-label": "Resolution note" });
      return h("div", { class: "clear-form" }, note, h("button", { type: "button", class: "primary", onclick: () => doClear(it, note.value) }, "Clear issue"),
        h("button", { type: "button", onclick: () => { clearing = null; render(); } }, "Cancel"));
    }
    if (it.source === "manual") parts.push(h("button", { type: "button", onclick: () => { clearing = it.id; approving = null; render(); }, title: "Clear with a resolution note" }, "Clear…"));
    else parts.push(h("span", { class: "muted", title: "System issues clear automatically when the underlying problem is fixed at its source." }, "Clears automatically"));
    if (canApprove() && !locked()) parts.push(h("button", { type: "button", onclick: () => { approving = it.id; clearing = null; render(); }, title: "Accept this issue with a reason" }, "Approve…"));
    return h("div", { class: "row" }, ...parts);
  }

  function resolution(it) {
    if (it.status === "cleared") return (it.resolution_note || "") + (it.cleared_by_system ? " (automatic)" : it.cleared_by_name ? " — " + it.cleared_by_name : "");
    if (it.status === "approved") return "Approved by " + (it.approved_by_name || "?") + (it.approval_note ? ": " + it.approval_note : "");
    return "";
  }

  function issueRow(it) {
    const [cls, label] = SOURCE[it.source] || ["warn", it.source.toUpperCase()];
    return h("tr", {},
      h("td", {}, it.id), h("td", {}, h("span", { class: "chip " + cls }, label), it.is_above_materiality ? h("div", {}, h("span", { class: "chip bad", title: "Above overall materiality" }, "ABOVE MATERIALITY")) : null),
      h("td", { class: "wrap" }, h("b", {}, it.title), it.detail ? h("div", { class: "sub" }, it.detail) : null,
        it.created_by_name ? h("div", { class: "sub" }, "Raised by " + it.created_by_name) : null),
      h("td", { class: "num" }, it.amount === null ? "" : App.money(it.amount)),
      h("td", {}, h("span", { class: "chip " + (STATUS_CLASS[it.status] || "ok") }, it.status.toUpperCase())),
      h("td", {}, ownerCell(it)),
      h("td", {}, new Date(it.last_seen_at).toLocaleString()), h("td", { class: "wrap" }, resolution(it)), h("td", {}, actionCell(it)));
  }

  function render() {
    renderSummary();
    const f = filters();
    const items = data.items.filter(i => (!f.source || i.source === f.source) && (!f.status || i.status === f.status));
    const tbody = document.querySelector("#issues-table tbody");
    if (!items.length) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 9, class: "muted" }, "No issues match these filters."))); return; }
    if (!f.group) { tbody.replaceChildren(...items.map(issueRow)); return; }
    const byCause = new Map();
    items.forEach(i => { if (!byCause.has(i.cause_code)) byCause.set(i.cause_code, { label: i.cause, rows: [] }); byCause.get(i.cause_code).rows.push(i); });
    const rows = [];
    [...byCause.entries()].sort((a, b) => a[1].label.localeCompare(b[1].label)).forEach(([code, g]) => {
      const open = g.rows.filter(r => r.status === "open").length, fold = collapsed.has(code);
      rows.push(h("tr", { class: "group-head", tabindex: 0, role: "button", "aria-expanded": String(!fold),
        onclick: () => { fold ? collapsed.delete(code) : collapsed.add(code); render(); } },
        h("td", { colspan: 9 }, (fold ? "▸ " : "▾ ") + g.label + " — " + g.rows.length + " issue(s), " + open + " open")));
      if (!fold) g.rows.forEach(r => rows.push(issueRow(r)));
    });
    tbody.replaceChildren(...rows);
  }

  async function load() {
    data = await App.api("/api/engagements/" + eid() + "/issues");
    if (data.sync_needed) {                                          // e.g. just after an upgrade: bring check issues up to date once
      try { await App.api("/api/engagements/" + eid() + "/checks/run", { method: "POST" }); data = await App.api("/api/engagements/" + eid() + "/issues"); } catch (e) { /* shown as-is */ }
    }
    render();
  }

  async function doClear(it, note) {
    try {
      await App.api("/api/engagements/" + eid() + "/issues/" + it.id + "/clear", { method: "POST", body: { resolution_note: note } });
      clearing = null; App.toast("Issue cleared."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function doApprove(it, note) {
    try {
      await App.api("/api/engagements/" + eid() + "/issues/" + it.id + "/approve", { method: "POST", body: { approval_note: note } });
      approving = null; App.toast("Issue approved. It no longer blocks sign-off."); await App.refreshHeader(); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function assign(it, value) {
    try {
      await App.api("/api/engagements/" + eid() + "/issues/" + it.id + "/assign", { method: "PATCH", body: { owner_id: value ? Number(value) : null } });
      App.toast(value ? "Owner assigned." : "Owner removed."); await load();
    } catch (err) { App.toast(err.message, true); await load(); }
  }

  async function raise() {
    const title = document.getElementById("new-title"), detail = document.getElementById("new-detail"), amount = document.getElementById("new-amount");
    try {
      await App.api("/api/engagements/" + eid() + "/issues", { method: "POST", body: { title: title.value, detail: detail.value || null, amount: amount.value.trim() || null } });
      title.value = detail.value = amount.value = ""; App.toast("Issue raised.");
      document.getElementById("f-status").value = "open"; await load();
    } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    ["f-source", "f-status", "f-group"].forEach(id => document.getElementById(id).addEventListener("change", render));
    document.getElementById("new-save").addEventListener("click", raise);
    const wanted = new URLSearchParams(location.search).get("source");            // e.g. /issues?source=gstr9c from the GSTR-9C screen
    if (wanted && [...document.getElementById("f-source").options].some(o => o.value === wanted)) document.getElementById("f-source").value = wanted;
    App.onChange(what => { if (data && (what === "unit" || what === "user")) render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
