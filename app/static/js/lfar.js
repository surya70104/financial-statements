/* LFAR assistant (T-24): live loan scrutiny against the adjusted TB, and the checklist with live figures, status and remarks. */
(function () {
  const { h } = App;
  let data = null;
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/lfar" + (tail || "");
  const AMT = /^\d{1,16}(\.\d{1,2})?$/, RATE = /^\d{1,3}(\.\d{1,3})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const locked = () => !!App.state.engagement.is_locked;
  const STATUS = [["pending", "Pending"], ["complied", "Complied"], ["exception_noted", "Exception noted"], ["not_applicable", "Not applicable"]];
  const FLAG = { over_limit: ["bad", "OVER LIMIT"], overdue: ["bad", "OVERDUE"], ledger_missing: ["warn", "LEDGER NOT IN TB"] };
  const stat = (label, value, hint, cls) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value " + (cls || "") }, value), hint ? h("div", { class: "hint" }, hint) : null);
  const drafts = {};                                                    // unsaved status / remarks per item id

  function renderCards() {
    const s = data.summary;
    $("l-cards").replaceChildren(stat("LFAR checkpoints reviewed", s.checkpoints_reviewed + " / " + s.checkpoints_total, "pending ones still need your conclusion"),
      stat("Exceptions noted", String(s.exceptions_noted), "", s.exceptions_noted ? "bad" : ""),
      stat("Total sanctioned limits", App.money(s.total_sanctioned_limits), s.facilities + " facility(ies)"),
      stat("Live adjusted TB borrowings", App.money(s.live_borrowings), s.over_limit + " over limit · " + s.overdue + " overdue", s.over_limit || s.overdue ? "bad" : ""));
  }

  function renderLoans() {
    $("l-table").querySelector("tbody").replaceChildren(...(data.loans.length ? data.loans.map(l => h("tr", { class: l.flags.includes("over_limit") || l.flags.includes("overdue") ? "mismatch" : "" },
      h("td", { class: "wrap" }, h("b", {}, l.lender_name), h("div", { class: "muted" }, l.account_number)), h("td", { class: "wrap" }, l.facility_type), h("td", { class: "wrap" }, l.ledger_code),
      h("td", { class: "num" }, Money.exact(l.sanctioned_limit)),
      h("td", { class: "num" }, l.book_balance === null ? "—" : Money.exact(l.book_balance), l.adjustment_net !== null && Money.toPaise(l.adjustment_net) !== 0n ? h("div", { class: "muted" }, "incl. adjustments " + Money.exact(l.adjustment_net)) : null),
      h("td", { class: "num" }, l.utilization_pct === null ? "—" : l.utilization_pct + " %"), h("td", {}, l.interest_rate === null ? "—" : l.interest_rate + " %"), h("td", { class: "wrap" }, l.security_details || "—"),
      h("td", { class: "wrap" }, l.emi_status + (Money.toPaise(l.overdue_amount) > 0n ? " · " + Money.exact(l.overdue_amount) : "")),
      h("td", {}, ...(l.flags.length ? l.flags.map(f => h("span", { class: "chip " + FLAG[f][0], style: "margin-right:4px" }, FLAG[f][1])) : [h("span", { class: "chip ok" }, "OK")])),
      h("td", { class: "no-print" }, !locked() ? h("button", { type: "button", onclick: () => removeLoan(l) }, "Delete") : null)))
      : [h("tr", {}, h("td", { colspan: 11, class: "muted" }, "No loan facilities yet. Upload the loan schedule or add a facility."))]));
  }

  function itemRow(it) {
    const m = it.auto_metric, d = drafts[it.id] || {};
    const status = d.status || it.status, remarks = d.remarks !== undefined ? d.remarks : (it.auditor_remarks || "");
    const hint = h("div", { class: "muted", hidden: true }, "Remarks are required for an exception.");
    const save = h("button", { type: "button", class: "primary", disabled: true, onclick: () => saveItem(it) }, "Save");
    const refresh = () => {                                           // update in place: rebuilding the row would drop the cursor from the box
      const dd = drafts[it.id] || {}, st = dd.status || it.status, rm = dd.remarks !== undefined ? dd.remarks : (it.auditor_remarks || "");
      const need = st === "exception_noted" && !rm.trim();
      hint.hidden = !need; save.disabled = locked() || it.id === null || !drafts[it.id] || need;
    };
    const sel = h("select", { disabled: locked() || it.id === null, onchange: e => { (drafts[it.id] = drafts[it.id] || {}).status = e.target.value; refresh(); } },
      ...STATUS.map(([v, t]) => h("option", { value: v, selected: v === status }, t)));
    const box = h("textarea", { rows: 2, maxlength: 2000, placeholder: "Auditor remarks", disabled: locked() || it.id === null, style: "width:100%",
      oninput: e => { (drafts[it.id] = drafts[it.id] || {}).remarks = e.target.value; refresh(); } }, remarks);
    refresh();
    return h("tr", {}, h("td", { class: "wrap" }, h("b", {}, it.section_code + " · " + it.category), h("div", { class: "muted" }, it.question_text)),
      h("td", { class: "wrap" }, m ? [h("div", { class: "muted" }, m.label), h("b", {}, m.value === null ? "—" : Money.exact(m.value)), " ",
        m.flag ? h("span", { class: "chip " + (m.flag === "ok" ? "ok" : "warn") }, m.flag === "ok" ? "OK" : "CHECK") : null, h("div", { class: "muted" }, m.detail)] : "—"),
      h("td", {}, sel, it.reviewed_by_name ? h("div", { class: "muted" }, it.reviewed_by_name + ", " + new Date(it.reviewed_at).toLocaleDateString()) : null),
      h("td", { style: "min-width:260px" }, box, hint), h("td", { class: "no-print" }, save));
  }

  function renderItems() { $("c-table").querySelector("tbody").replaceChildren(...data.items.map(itemRow)); }

  function renderControls() {
    const lock = locked();
    $("l-upload").disabled = lock || !$("l-file").files.length;
    const ok = $("l-lender").value.trim() && $("l-acct").value.trim() && $("l-ledger").value.trim() && AMT.test(clean($("l-limit").value))
      && (!clean($("l-rate").value) || RATE.test(clean($("l-rate").value))) && (!clean($("l-overdue").value) || AMT.test(clean($("l-overdue").value)));
    $("l-add").disabled = lock || !ok;
    $("l-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before the schedule or checklist can change." : "";
  }

  const render = () => { renderCards(); renderLoans(); renderItems(); renderControls(); };
  async function load() { data = await App.api(url()); render(); }

  async function loadLedgers() {
    try {
      const a = await App.api("/api/engagements/" + eid() + "/adjustments");
      $("l-ledgers").replaceChildren(...a.ledgers.map(l => h("option", { value: l.name })));
    } catch (e) { /* only a convenience */ }
  }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("l-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message,
      h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function upload() {
    const f = $("l-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("l-upload").disabled = true;
    try {
      const r = await App.api(url("/loans/import"), { method: "POST", body: fd });
      $("l-errors").replaceChildren(); $("l-file").value = ""; App.toast(r.imported + " facility(ies) imported (" + r.added + " new, " + r.updated + " updated)."); await load();
    } catch (err) { showErrors(err); App.toast(err.message, true); renderControls(); }
  }

  async function addLoan() {
    try {
      await App.api(url("/loans"), { method: "POST", body: { lender_name: $("l-lender").value, facility_type: $("l-type").value, account_number: $("l-acct").value,
        ledger_code: $("l-ledger").value, sanctioned_limit: clean($("l-limit").value), interest_rate: clean($("l-rate").value) || null, security_details: $("l-security").value || null,
        emi_status: $("l-emi").value, overdue_amount: clean($("l-overdue").value) || null } });
      ["l-lender", "l-acct", "l-ledger", "l-limit", "l-rate", "l-overdue", "l-security"].forEach(id => { $(id).value = ""; });
      App.toast("Facility saved."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function removeLoan(l) {
    if (!window.confirm("Remove " + l.lender_name + " (" + l.account_number + ") from the schedule?")) return;
    try { await App.api(url("/loans/" + l.id), { method: "DELETE" }); await load(); } catch (err) { App.toast(err.message, true); }
  }

  async function saveItem(it) {
    const d = drafts[it.id] || {};
    try {
      await App.api(url("/items/" + it.id), { method: "PATCH", body: { status: d.status || it.status, auditor_remarks: d.remarks !== undefined ? d.remarks : (it.auditor_remarks || "") } });
      delete drafts[it.id]; App.toast(it.section_code + " saved."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    $("l-file").addEventListener("change", () => data && renderControls());
    ["l-lender", "l-acct", "l-ledger", "l-limit", "l-rate", "l-overdue"].forEach(id => $(id).addEventListener("input", () => data && renderControls()));
    $("l-upload").addEventListener("click", upload); $("l-add").addEventListener("click", addLoan);
    App.onChange(what => { if (data && (what === "user" || what === "unit")) render(); });
    await load(); loadLedgers();
  }).catch(err => App.toast(err.message, true));
})();
