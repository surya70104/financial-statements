/* Reconciliation engine (T-20): summary, upload / add items, Book vs Statement vs Difference, create DRAFT adjustments. */
(function () {
  const { h } = App;
  let data = null;
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/reconciliations" + (tail || "");
  const AMT = /^-?\d{1,16}(\.\d{1,2})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const locked = () => !!App.state.engagement.is_locked;
  const dmy = s => (s ? s.split("-").reverse().join("-") : "—");
  const signed = v => Money.exact(v);
  const fromPaise = p => (p < 0n ? "-" : "") + ((p < 0n ? -p : p) / 100n) + "." + ((p < 0n ? -p : p) % 100n).toString().padStart(2, "0");
  const stat = (label, value, hint) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);

  function renderCards() {
    const s = data.summary;
    $("r-cards").replaceChildren(stat("Total items", String(s.total_items)), stat("Timing differences", String(s.timing_differences), "no entry needed · net " + Money.exact(s.timing_amount)),
      stat("Adjustment required", String(s.adjustment_required), "net " + Money.exact(s.adjustment_amount)),
      stat("Proposed", String(s.proposed_count), s.approved_count + " approved · " + s.awaiting_proposal + " waiting"));
    $("r-status").replaceChildren(h("span", { class: "chip " + (s.awaiting_proposal ? "warn" : "ok") }, s.awaiting_proposal + " waiting for a proposal"));
  }

  function statusCell(r) {
    if (r.status === "open") return h("span", { class: "chip warn" }, "OPEN");
    if (r.status === "proposed") return h("span", null, h("span", { class: "chip warn" }, "PROPOSED"), " ", h("a", { href: "/adjustments" }, "Draft JE #" + r.journal_number));
    if (r.proposed_journal_id) return h("span", null, h("span", { class: "chip ok" }, "RESOLVED"), " ", h("a", { href: "/adjustments" }, "JE #" + r.journal_number + " approved"));
    return h("span", { class: "chip ok" }, r.adjustment_action === "timing_only" ? "TIMING ONLY" : "MATCHED");
  }

  function renderTable() {
    $("r-table").querySelector("tbody").replaceChildren(...(data.rows.length ? data.rows.map(r => h("tr", {},
      h("td", {}, r.id), h("td", {}, dmy(r.item_date)), h("td", { class: "wrap" }, r.account_name), h("td", {}, r.reference_no || "—"), h("td", { class: "wrap" }, r.description),
      h("td", { class: "num" }, signed(r.book_amount)), h("td", { class: "num" }, signed(r.external_amount)), h("td", { class: "num" }, h("b", {}, signed(r.difference_amount))),
      h("td", { class: "wrap" }, r.adjustment_action === "timing_only" ? "Timing only" : "Propose adjustment" + (r.ledger_code ? " → " + r.ledger_code : "")),
      h("td", {}, statusCell(r)),
      h("td", {}, !r.proposed_journal_id && !locked() ? h("button", { type: "button", onclick: () => remove(r) }, "Delete") : null)))
      : [h("tr", {}, h("td", { colspan: 11, class: "muted" }, "No items yet. Upload a file or add an item."))]));
  }

  function renderControls() {
    const lock = locked();
    $("r-upload").disabled = lock || !$("r-file").files.length;
    const book = clean($("r-book").value), ext = clean($("r-ext").value);
    const ok = $("r-account").value.trim() && $("r-date").value && $("r-desc").value.trim() && AMT.test(book) && AMT.test(ext);
    $("r-add").disabled = lock || !ok;
    $("r-calc").textContent = AMT.test(book) && AMT.test(ext) ? "Difference: " + Money.exact(fromPaise(Money.toPaise(ext) - Money.toPaise(book))) : "";
    $("r-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before items can change." : "";
    const waiting = data.summary.awaiting_proposal;
    let why = "";
    if (lock) why = "Locked after Partner sign-off.";
    else if (!waiting) why = "No open items to propose.";
    $("r-propose").disabled = !!why; $("r-prop-hint").textContent = why || (waiting + " item(s) ready: drafts for a Checker or Partner to approve");
  }

  const render = () => { renderCards(); renderTable(); renderControls(); };
  async function load() { data = await App.api(url()); render(); }

  async function loadLedgers() {
    try {
      const a = await App.api("/api/engagements/" + eid() + "/adjustments");
      $("r-ledgers").replaceChildren(...a.ledgers.map(l => h("option", { value: l.name })));
    } catch (e) { /* the list is only a convenience */ }
  }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("r-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message,
      h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function upload() {
    const f = $("r-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("r-upload").disabled = true;
    try {
      const r = await App.api(url("/import"), { method: "POST", body: fd });
      $("r-errors").replaceChildren(); $("r-file").value = ""; App.toast(r.imported + " item(s) imported, " + r.needing_adjustment + " need an adjustment."); await load();
    } catch (err) { showErrors(err); App.toast(err.message, true); renderControls(); }
  }

  async function add() {
    try {
      await App.api(url(), { method: "POST", body: { recon_type: $("r-type").value, account_name: $("r-account").value, item_date: $("r-date").value,
        reference_no: $("r-ref").value || null, description: $("r-desc").value, book_amount: clean($("r-book").value), external_amount: clean($("r-ext").value),
        adjustment_action: $("r-action").value, ledger_code: $("r-ledger").value || null, offset_ledger_code: $("r-offset").value || null } });
      ["r-ref", "r-desc", "r-book", "r-ext", "r-ledger", "r-offset"].forEach(id => { $(id).value = ""; });
      App.toast("Item added."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function remove(r) {
    if (!window.confirm("Delete item #" + r.id + " (" + r.description + ")?")) return;
    try { await App.api(url("/" + r.id), { method: "DELETE" }); await load(); } catch (err) { App.toast(err.message, true); }
  }

  async function propose() {
    $("r-propose").disabled = true;
    try {
      const r = await App.api(url("/propose-adjustments"), { method: "POST" });
      App.toast(r.proposed + " draft adjustment(s) created. A Checker or Partner approves them on the Adjustments tab.");
      await load();
    } catch (err) { App.toast(err.message, true); renderControls(); }
  }

  App.ready.then(async () => {
    $("r-file").addEventListener("change", () => data && renderControls());
    ["r-account", "r-date", "r-desc", "r-book", "r-ext"].forEach(id => $(id).addEventListener("input", () => data && renderControls()));
    $("r-upload").addEventListener("click", upload); $("r-add").addEventListener("click", add); $("r-propose").addEventListener("click", propose);
    App.onChange(what => { if (data && (what === "user" || what === "unit")) render(); });
    await load(); loadLedgers();
  }).catch(err => App.toast(err.message, true));
})();
