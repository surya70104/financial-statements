/* TDS / TCS entry pack (T-19): summary cards, upload / add rows, register, post to the adjusted TB. */
(function () {
  const { h } = App;
  let data = null;
  const $ = id => document.getElementById(id);
  const eid = () => App.state.engagement.id;
  const url = tail => "/api/engagements/" + eid() + "/tds-tcs" + (tail || "");
  const LABEL = { TDS_PAYABLE: "TDS payable", TDS_RECEIVABLE: "TDS receivable", TCS_PAYABLE: "TCS payable", TCS_RECEIVABLE: "TCS receivable" };
  const SIDE = { TDS_PAYABLE: "Current liability", TCS_PAYABLE: "Current liability", TDS_RECEIVABLE: "Current asset", TCS_RECEIVABLE: "Current asset" };
  const AMT = /^\d{1,16}(\.\d{1,2})?$/, PCT = /^\d{1,3}(\.\d{1,2})?$/;
  const clean = s => s.trim().replace(/,/g, "");
  const locked = () => !!App.state.engagement.is_locked;
  const canPost = () => !!App.state.user && App.state.user.roles.some(r => r === "CHECKER" || r === "PARTNER");
  const stat = (label, value, hint) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);

  function renderCards() {
    const s = data.summary.by_type;
    $("t-cards").replaceChildren(...Object.keys(LABEL).map(k => stat(LABEL[k], App.money(s[k].tax_amount),
      s[k].count + " row(s) · " + SIDE[k] + " · posted " + App.money(s[k].posted))));
    $("t-status").replaceChildren(h("span", { class: "chip warn" }, data.summary.draft_rows + " draft"), " ", h("span", { class: "chip ok" }, data.summary.posted_rows + " posted"));
  }

  function renderTable() {
    $("t-table").querySelector("tbody").replaceChildren(...(data.rows.length ? data.rows.map(r => h("tr", {},
      h("td", {}, r.id), h("td", {}, LABEL[r.entry_type]), h("td", {}, r.section_code), h("td", { class: "wrap" }, r.party_name),
      h("td", { class: "wrap" }, r.ledger_code || h("span", { class: "muted" }, data.plan[r.entry_type].ledger + " (default)")),
      h("td", { class: "num" }, Money.exact(r.base_amount)), h("td", { class: "num" }, r.tax_rate), h("td", { class: "num" }, Money.exact(r.tax_amount)),
      h("td", {}, r.status === "posted" ? h("span", { class: "chip ok" }, "POSTED · JE #" + r.journal_number) : h("span", { class: "chip warn" }, "DRAFT")),
      h("td", {}, r.created_by_name),
      h("td", {}, r.status === "draft" && !locked() ? h("button", { type: "button", onclick: () => remove(r) }, "Delete") : null)))
      : [h("tr", {}, h("td", { colspan: 11, class: "muted" }, "No rows yet. Upload a file or add a row."))]));
  }

  function renderControls() {
    const lock = locked();
    $("t-upload").disabled = lock || !$("t-file").files.length;
    const base = clean($("t-base").value), rate = clean($("t-rate").value);
    const ok = AMT.test(base) && PCT.test(rate) && $("t-section").value.trim() && $("t-party").value.trim();
    $("t-add").disabled = lock || !ok;
    if (AMT.test(base) && PCT.test(rate)) {
      const p = (Money.toPaise(base) * Money.toPaise(rate) + 5000n) / 10000n;
      $("t-calc").textContent = "Calculated tax: " + Money.exact(p / 100n + "." + (p % 100n).toString().padStart(2, "0"));
    } else $("t-calc").textContent = "";
    $("t-hint").textContent = lock ? "The engagement is signed off and locked; a Partner must re-open it before the register can change." : "";
    const drafts = data.rows.filter(r => r.status === "draft"), others = drafts.filter(r => r.created_by !== App.state.user.id);
    let why = "";
    if (lock) why = "Locked after Partner sign-off.";
    else if (!canPost()) why = "Only a Checker or Partner can post.";
    else if (!drafts.length) why = "No draft rows to post.";
    else if (!others.length) why = "You entered these rows; another user must post them.";
    $("t-post").disabled = !!why; $("t-post-hint").textContent = why || (others.length + " row(s) ready to post");
  }

  const render = () => { renderCards(); renderTable(); renderControls(); };
  async function load() { data = await App.api(url()); render(); }

  function showErrors(err) {
    const list = err.detail && err.detail.errors;
    $("t-errors").replaceChildren(...(list && list.length ? [h("div", { class: "banner", style: "margin:8px 0" }, err.message,
      h("ul", {}, ...list.map(e => h("li", {}, "Row " + e.row + ": " + e.message))))] : []));
  }

  async function upload() {
    const f = $("t-file").files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    $("t-upload").disabled = true;
    try {
      const r = await App.api(url("/import"), { method: "POST", body: fd });
      $("t-errors").replaceChildren(); $("t-file").value = ""; App.toast(r.imported + " row(s) imported as drafts."); await load();
    } catch (err) { showErrors(err); App.toast(err.message, true); renderControls(); }
  }

  async function add() {
    try {
      await App.api(url(), { method: "POST", body: { entry_type: $("t-type").value, section_code: $("t-section").value, party_name: $("t-party").value,
        ledger_code: $("t-ledger").value || null, base_amount: clean($("t-base").value), tax_rate: clean($("t-rate").value), tax_amount: clean($("t-tax").value) || null } });
      ["t-section", "t-party", "t-ledger", "t-base", "t-rate", "t-tax"].forEach(id => { $(id).value = ""; });
      App.toast("Row added as a draft."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  async function remove(r) {
    if (!window.confirm("Delete draft row #" + r.id + " (" + r.party_name + ")?")) return;
    try { await App.api(url("/" + r.id), { method: "DELETE" }); await load(); } catch (err) { App.toast(err.message, true); }
  }

  async function post() {
    $("t-post").disabled = true;
    try {
      const r = await App.api(url("/post"), { method: "POST", body: {} });
      await App.refreshHeader(); App.toast(r.posted + " row(s) posted to the adjusted trial balance." + (r.skipped_own ? " " + r.skipped_own + " of your own row(s) were left for another user." : ""));
      await load();
    } catch (err) { App.toast(err.message, true); renderControls(); }
  }

  App.ready.then(async () => {
    $("t-file").addEventListener("change", () => data && renderControls());
    ["t-section", "t-party", "t-base", "t-rate", "t-tax", "t-type", "t-ledger"].forEach(id => $(id).addEventListener("input", () => data && renderControls()));
    $("t-upload").addEventListener("click", upload); $("t-add").addEventListener("click", add); $("t-post").addEventListener("click", post);
    App.onChange(what => { if (data && (what === "user" || what === "unit")) render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
