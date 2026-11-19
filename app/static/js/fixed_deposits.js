/* Fixed deposit register (T-22): summary cards, upload / add FDs, register, post to the adjusted TB. */
(function () {
  const { h } = App;
  let data = null;
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/fixed-deposits" + (tail || "");
  const AMT = /^\d{1,16}(\.\d{1,2})?$/, PCT = /^\d{1,3}(\.\d{1,3})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const LINE = { "BS.CA.CASH": "Cash and cash equivalents", "BS.CA.OTH": "Other current assets", "BS.NCA.OTH": "Other non-current assets" };
  const locked = () => !!App.state.engagement.is_locked;
  const canPost = () => !!App.state.user && App.state.user.roles.some(r => r === "CHECKER" || r === "PARTNER");
  const stat = (label, value, hint) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);
  const dmy = s => s.split("-").reverse().join("-");

  function renderCards() {
    const s = data.summary;
    $("f-cards").replaceChildren(stat("Total principal", App.money(s.total_principal), s.count + " FD(s)"), stat("Current FDs", App.money(s.current_principal), "matures within 12 months"),
      stat("Non-current FDs", App.money(s.non_current_principal), "matures later"), stat("Accrued interest (FY " + data.period.fy + ")", App.money(s.total_accrued_interest), "to " + dmy(data.period.end)));
    $("f-status").replaceChildren(h("span", { class: "chip warn" }, s.draft_rows + " draft"), " ", h("span", { class: "chip ok" }, s.posted_rows + " posted"));
    $("f-period").textContent = "Balance sheet date " + dmy(data.period.end) + ". Interest is accrued from " + dmy(data.period.start) + " (or the FD start) to " + dmy(data.period.end) + ".";
  }

  function renderTable() {
    $("f-table").querySelector("tbody").replaceChildren(...(data.rows.length ? data.rows.map(r => h("tr", {},
      h("td", {}, r.id), h("td", {}, r.bank_name), h("td", {}, r.fd_number), h("td", { class: "num" }, Money.exact(r.principal_amount)), h("td", { class: "num" }, r.interest_rate),
      h("td", {}, dmy(r.start_date)), h("td", {}, dmy(r.maturity_date)),
      h("td", {}, h("span", { class: "chip " + (r.maturity_classification === "current" ? "ok" : "warn") }, r.maturity_classification === "current" ? "Current" : "Non-current")),
      h("td", { class: "wrap" }, LINE[r.schedule_line_code]), h("td", { class: "num" }, Money.exact(r.accrued_interest)),
      h("td", {}, r.status === "posted" ? h("span", { class: "chip ok" }, "POSTED · JE #" + r.journal_number) : h("span", { class: "chip warn" }, "DRAFT")),
      h("td", {}, r.status === "draft" && !locked() ? h("button", { type: "button", onclick: () => remove(r) }, "Delete") : null)))
      : [h("tr", {}, h("td", { colspan: 12, class: "muted" }, "No FDs yet. Upload a file or add an FD."))]));
  }

  function renderControls() {
    const lock = locked();
    $("f-upload").disabled = lock || !$("f-file").files.length;
    const ok = $("f-bank").value.trim() && $("f-no").value.trim() && AMT.test(clean($("f-principal").value)) && PCT.test(clean($("f-rate").value)) && $("f-start").value && $("f-maturity").value;
    $("f-add").disabled = lock || !ok;
    $("f-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before the register can change." : "";
    const drafts = data.rows.filter(r => r.status === "draft"), others = drafts.filter(r => r.created_by !== App.state.user.id);
    let why = "";
    if (lock) why = "Locked after Partner sign-off.";
    else if (!canPost()) why = "Only a Checker or Partner can post.";
    else if (!drafts.length) why = "No draft FDs to post.";
    else if (!others.length) why = "You entered these FDs; another user must post them.";
    $("f-post").disabled = !!why; $("f-post-hint").textContent = why || (others.length + " FD(s) ready to post");
  }

  const render = () => { renderCards(); renderTable(); renderControls(); };
  async function load() { data = await App.api(url()); render(); }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("f-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message,
      h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function upload() {
    const f = $("f-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("f-upload").disabled = true;
    try {
      const r = await App.api(url("/import"), { method: "POST", body: fd });
      $("f-errors").replaceChildren(); $("f-file").value = ""; App.toast(r.imported + " FD(s) imported as drafts."); await load();
    } catch (err) { showErrors(err); App.toast(err.message, true); renderControls(); }
  }

  async function add() {
    try {
      await App.api(url(), { method: "POST", body: { bank_name: $("f-bank").value, fd_number: $("f-no").value, principal_amount: clean($("f-principal").value),
        interest_rate: clean($("f-rate").value), start_date: $("f-start").value, maturity_date: $("f-maturity").value, schedule_line_code: $("f-line").value || null,
        accrued_interest: clean($("f-accrued").value) || null, ledger_code: $("f-ledger").value || null } });
      ["f-bank", "f-no", "f-principal", "f-rate", "f-start", "f-maturity", "f-accrued", "f-ledger"].forEach(id => { $(id).value = ""; });
      App.toast("FD added as a draft."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function remove(r) {
    if (!window.confirm("Delete draft FD " + r.fd_number + " (" + r.bank_name + ")?")) return;
    try { await App.api(url("/" + r.id), { method: "DELETE" }); await load(); } catch (err) { App.toast(err.message, true); }
  }

  async function post() {
    $("f-post").disabled = true;
    try {
      const r = await App.api(url("/post"), { method: "POST", body: {} });
      await App.refreshHeader(); App.toast(r.posted + " FD(s) posted to the adjusted trial balance." + (r.skipped_own ? " " + r.skipped_own + " of your own FD(s) were left for another user." : ""));
      await load();
    } catch (err) { App.toast(err.message, true); renderControls(); }
  }

  App.ready.then(async () => {
    $("f-file").addEventListener("change", () => data && renderControls());
    ["f-bank", "f-no", "f-principal", "f-rate", "f-start", "f-maturity", "f-line", "f-accrued", "f-ledger"].forEach(id => $(id).addEventListener("input", () => data && renderControls()));
    $("f-upload").addEventListener("click", upload); $("f-add").addEventListener("click", add); $("f-post").addEventListener("click", post);
    App.onChange(what => { if (data && (what === "user" || what === "unit")) render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
