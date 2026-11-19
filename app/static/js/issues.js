/* Unified issue tracker (T-12). */
(function () {
  const { h } = App;
  let data = null;
  let clearing = null;                                // id of the manual issue whose clear form is open
  const eid = () => App.state.engagement.id;
  const SOURCE = { check: ["bad", "CHECK"], mapping: ["warn", "MAPPING"], materiality: ["warn", "MATERIALITY"], manual: ["ok", "MANUAL"] };

  function filters() { return { source: document.getElementById("f-source").value, status: document.getElementById("f-status").value }; }

  function renderSummary() {
    const s = data.summary;
    document.getElementById("issue-summary").replaceChildren(
      h("span", { class: "chip " + (s.open ? "bad" : "ok") }, s.open + " open"), h("span", { class: "chip ok" }, s.cleared + " cleared"),
      ...["check", "mapping", "materiality", "manual"].map(k => h("span", { class: "muted" }, k + ": " + s.by_source[k].open + " open")));
    App.setIssuesBadge(s);
  }

  function clearCell(it) {
    if (it.status !== "open") return h("span", { class: "muted" }, "");
    if (it.source !== "manual") return h("span", { class: "muted", title: "System issues clear automatically when the underlying problem is fixed at its source." }, "Clears automatically");
    if (clearing === it.id) {
      const note = h("input", { type: "text", placeholder: "Resolution note (required)", maxlength: 500, "aria-label": "Resolution note" });
      const ok = h("button", { type: "button", class: "primary", onclick: () => doClear(it, note.value) }, "Clear issue");
      return h("div", { class: "clear-form" }, note, ok, h("button", { type: "button", onclick: () => { clearing = null; render(); } }, "Cancel"));
    }
    return h("button", { type: "button", onclick: () => { clearing = it.id; render(); }, title: "Clear with a resolution note" }, "Clear…");
  }

  function render() {
    renderSummary();
    const f = filters();
    const items = data.items.filter(i => (!f.source || i.source === f.source) && (!f.status || i.status === f.status));
    const tbody = document.querySelector("#issues-table tbody");
    if (!items.length) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 8, class: "muted" }, "No issues match these filters."))); return; }
    tbody.replaceChildren(...items.map(it => {
      const [cls, label] = SOURCE[it.source] || ["warn", it.source.toUpperCase()];
      const res = it.status === "cleared"
        ? (it.resolution_note || "") + (it.cleared_by_system ? " (automatic)" : it.cleared_by_name ? " — " + it.cleared_by_name : "")
        : "";
      return h("tr", {},
        h("td", {}, it.id), h("td", {}, h("span", { class: "chip " + cls }, label)),
        h("td", { class: "wrap" }, h("b", {}, it.title), it.detail ? h("div", { class: "sub" }, it.detail) : null,
          it.created_by_name ? h("div", { class: "sub" }, "Raised by " + it.created_by_name) : null),
        h("td", { class: "num" }, it.amount === null ? "" : App.money(it.amount)),
        h("td", {}, h("span", { class: "chip " + (it.status === "open" ? "bad" : "ok") }, it.status.toUpperCase())),
        h("td", {}, new Date(it.last_seen_at).toLocaleString()), h("td", { class: "wrap" }, res), h("td", {}, clearCell(it)));
    }));
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

  async function raise() {
    const title = document.getElementById("new-title"), detail = document.getElementById("new-detail"), amount = document.getElementById("new-amount");
    try {
      await App.api("/api/engagements/" + eid() + "/issues", { method: "POST", body: { title: title.value, detail: detail.value || null, amount: amount.value.trim() || null } });
      title.value = detail.value = amount.value = ""; App.toast("Issue raised.");
      document.getElementById("f-status").value = "open"; await load();
    } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    ["f-source", "f-status"].forEach(id => document.getElementById(id).addEventListener("change", render));
    document.getElementById("new-save").addEventListener("click", raise);
    App.onChange(what => { if (data && what === "unit") render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
