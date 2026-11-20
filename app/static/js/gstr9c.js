/* GSTR-9C reconciliation (T-21): books vs GSTR-9C per GST head and table; differences are logged once in the Issue Tracker. */
(function () {
  const { h } = App;
  let data = null;
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/gstr9c" + (tail || "");
  const AMT = /^-?\d{1,16}(\.\d{1,2})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const locked = () => !!App.state.engagement.is_locked;
  const stat = (label, value, hint, cls) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value " + (cls || "") }, value), hint ? h("div", { class: "hint" }, hint) : null);
  const fromPaise = p => (p < 0n ? "-" : "") + ((p < 0n ? -p : p) / 100n) + "." + ((p < 0n ? -p : p) % 100n).toString().padStart(2, "0");

  function renderCards() {
    const s = data.summary;
    $("g-cards").replaceChildren(stat("Books GST", Money.exact(s.books_total), s.rows + " row(s)"), stat("GSTR-9C reported", Money.exact(s.gstr9c_total)),
      stat("Net difference", Money.exact(s.net_difference), "books − GSTR-9C", Money.toPaise(s.net_difference) === 0n ? "" : "bad"),
      stat("Open GST issues", String(s.open_issues), s.matched + " matched · " + s.with_difference + " with a difference", s.open_issues ? "bad" : ""));
    $("g-status").replaceChildren(h("span", { class: "chip " + (s.open_issues ? "bad" : "ok") }, s.open_issues ? s.open_issues + " open in Issues" : "All matched"));
  }

  function renderTable() {
    const chip = r => r.status === "difference_logged" ? h("span", { class: "chip bad" }, "DIFFERENCE LOGGED" + (r.issue_id ? " · issue #" + r.issue_id : ""))
      : r.status === "resolved" ? h("span", { class: "chip ok" }, "RESOLVED") : h("span", { class: "chip ok" }, "MATCHED");
    $("g-table-el").querySelector("tbody").replaceChildren(...(data.rows.length ? data.rows.map(r => h("tr", { class: r.status === "difference_logged" ? "mismatch" : "" },
      h("td", {}, r.id), h("td", {}, h("b", {}, r.gst_head)), h("td", {}, r.table_ref), h("td", { class: "wrap" }, r.description || "—"),
      h("td", { class: "num" }, Money.exact(r.books_amount)), h("td", { class: "num" }, Money.exact(r.gstr9c_amount)), h("td", { class: "num" }, h("b", {}, Money.exact(r.difference_amount))),
      h("td", {}, chip(r), r.status === "difference_logged" ? [" ", h("a", { href: "/issues?source=gstr9c" }, "view")] : null),
      h("td", {}, !locked() ? h("button", { type: "button", onclick: () => remove(r) }, "Delete") : null)))
      : [h("tr", {}, h("td", { colspan: 9, class: "muted" }, "No rows yet. Upload a file or add a row."))]));
  }

  function renderControls() {
    const lock = locked();
    $("g-upload").disabled = lock || !$("g-file").files.length;
    const b = clean($("g-books").value), n = clean($("g-nine").value);
    $("g-add").disabled = lock || !($("g-table").value.trim() && AMT.test(b) && AMT.test(n));
    $("g-calc").textContent = AMT.test(b) && AMT.test(n) ? "Difference: " + Money.exact(fromPaise(Money.toPaise(b) - Money.toPaise(n))) : "";
    $("g-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before rows can change." : "";
  }

  const render = () => { renderCards(); renderTable(); renderControls(); };
  async function load() { data = await App.api(url()); render(); }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("g-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message,
      h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function refresh() { await App.refreshHeader(); await load(); }              // the header's issue badge changes with every import

  async function upload() {
    const f = $("g-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("g-upload").disabled = true;
    try {
      const r = await App.api(url("/import"), { method: "POST", body: fd });
      $("g-errors").replaceChildren(); $("g-file").value = "";
      App.toast(r.imported + " row(s) imported (" + r.added + " new, " + r.updated + " updated). " + r.differences + " difference(s) logged in Issues.");
      await refresh();
    } catch (err) { showErrors(err); App.toast(err.message, true); renderControls(); }
  }

  async function add() {
    try {
      const r = await App.api(url(), { method: "POST", body: { gst_head: $("g-head").value, table_ref: $("g-table").value, description: $("g-desc").value || null,
        books_amount: clean($("g-books").value), gstr9c_amount: clean($("g-nine").value) } });
      ["g-desc", "g-books", "g-nine"].forEach(id => { $(id).value = ""; });
      App.toast("Row " + r.result + "."); await refresh();
    } catch (err) { App.toast(err.message, true); }
  }

  async function remove(r) {
    if (!window.confirm("Delete " + r.gst_head + " / " + r.table_ref + "?")) return;
    try { await App.api(url("/" + r.id), { method: "DELETE" }); await refresh(); } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    $("g-file").addEventListener("change", () => data && renderControls());
    ["g-table", "g-books", "g-nine"].forEach(id => $(id).addEventListener("input", () => data && renderControls()));
    $("g-upload").addEventListener("click", upload); $("g-add").addEventListener("click", add);
    App.onChange(what => { if (data && (what === "user" || what === "unit")) render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
