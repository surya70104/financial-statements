/* Prior-year comparatives (on the Trial Balance tab): upload ledger_name, prior_year_amount, or edit each ledger's prior-year balance inline. */
(function () {
  const { h } = App;
  let data = null;
  const edits = new Map();                                        // ledger_id -> text typed so far (only ledgers the user changed)
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/prior-year" + (tail || "");
  const AMT = /^-?\d{1,16}(\.\d{1,2})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const locked = () => !!App.state.engagement.is_locked;

  const shown = r => (r.prior_year_amount === null ? "" : r.prior_year_amount);

  function renderSummary() {
    const diff = data.previous_difference, balanced = Money.toPaise(diff) === 0n;
    $("py-summary").replaceChildren(h("span", { class: "chip " + (data.entered ? "ok" : "warn") }, data.entered + " of " + data.total + " ledgers have a " + data.previous_label + " balance"), " ",
      data.entered ? h("span", { class: "chip " + (balanced ? "ok" : "bad") }, balanced ? "✓ " + data.previous_label + " Balance Sheet balances" : data.previous_label + " Balance Sheet differs by " + Money.exact(diff)) : null);
  }

  function renderTable() {
    const q = $("py-filter").value.trim().toLowerCase();
    const rows = data.ledgers.filter(r => !q || (r.name + " " + (r.tally_group || "")).toLowerCase().includes(q));
    $("py-table").querySelector("tbody").replaceChildren(...rows.map(r => {
      const dirty = edits.has(r.ledger_id);
      const input = h("input", { type: "text", inputmode: "decimal", value: dirty ? edits.get(r.ledger_id) : shown(r), placeholder: "—", "aria-label": "Prior-year amount for " + r.name,
        disabled: locked(), style: "width:150px; text-align:right", oninput: e => { edits.set(r.ledger_id, e.target.value); update(); } });
      return h("tr", { class: dirty ? "extra" : "" }, h("td", { class: "wrap" }, r.name), h("td", {}, r.tally_group || ""), h("td", {}, r.side),
        h("td", { class: "num" }, r.current_amount === null ? "—" : Money.exact(r.current_amount)), h("td", { class: "num" }, input),
        h("td", { class: "wrap" }, r.line_label || h("span", { class: "muted" }, "not mapped")));
    }));
  }

  function bad() { return [...edits.values()].some(v => clean(v) !== "" && !AMT.test(clean(v))); }

  function update() {
    const lock = locked();
    $("py-save").disabled = lock || !edits.size || bad();
    $("py-upload").disabled = lock || !$("py-file").files.length;
    $("py-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before comparatives can change."
      : bad() ? "Use numbers with up to 2 decimals (a minus sign is the opposite side)." : (edits.size ? edits.size + " unsaved change(s)" : "");
  }

  const render = () => { renderSummary(); renderTable(); update(); };
  async function load() { data = await App.api(url()); render(); }

  async function save() {
    $("py-save").disabled = true;
    try {
      const items = [...edits.entries()].map(([ledger_id, v]) => ({ ledger_id, amount: clean(v) || null }));
      const r = await App.api(url(), { method: "POST", body: { items } });
      edits.clear(); data = r; await App.refreshHeader(); render();
      App.toast(r.updated + " ledger(s) updated" + (r.cleared ? ", " + r.cleared + " cleared" : "") + ". The statements now show the new comparatives.");
    } catch (err) { App.toast(err.message, true); update(); }
  }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("py-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message, h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function upload() {
    const f = $("py-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("py-upload").disabled = true;
    try {
      const r = await App.api(url("/import"), { method: "POST", body: fd });
      $("py-errors").replaceChildren(); $("py-file").value = ""; edits.clear(); data = r; await App.refreshHeader(); render();
      App.toast(r.imported + " prior-year balance(s) imported. The statements now show the new comparatives.");
    } catch (err) { showErrors(err); App.toast(err.message, true); update(); }
  }

  App.ready.then(async () => {
    $("py-file").addEventListener("change", () => data && update());
    $("py-filter").addEventListener("input", () => data && renderTable());
    $("py-upload").addEventListener("click", upload); $("py-save").addEventListener("click", save);
    App.onChange(what => { if (data && what === "unit") render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
