/* Adjustments (T-04, T-05) and the adjusted trial balance (T-06). Amount maths is exact (BigInt paise). */
(function () {
  const { h } = App;
  let data = null, atb = null;
  const rows = [];                                   // form rows: {ledger, debit, credit, note}
  const eid = () => App.state.engagement.id;
  const roles = () => (App.state.user ? App.state.user.roles : []);
  const canCreate = () => !App.isLocked() && roles().some(r => r === "MAKER" || r === "PARTNER");
  const canApprove = () => !App.isLocked() && roles().some(r => r === "CHECKER" || r === "PARTNER");

  const AMOUNT = /^\d+(\.\d{1,2})?$/;
  const paise = s => (s && AMOUNT.test(s.trim()) ? Money.toPaise(s.trim()) : null);

  // ---------- entry form ----------
  function totals() {
    let dr = 0n, cr = 0n, bad = false, complete = 0;
    rows.forEach(r => {
      const d = r.debit.trim(), c = r.credit.trim();
      if (d && paise(d) === null) bad = true;
      if (c && paise(c) === null) bad = true;
      const dp = paise(d) || 0n, cp = paise(c) || 0n;
      dr += dp; cr += cp;
      if (r.ledger && ((dp > 0n) !== (cp > 0n))) complete++;
    });
    return { dr, cr, bad, complete };
  }

  function updateFooter() {
    const t = totals(), diff = t.dr - t.cr;
    document.getElementById("tot-dr").textContent = Money.exact(fixed(t.dr));
    document.getElementById("tot-cr").textContent = Money.exact(fixed(t.cr));
    const diffEl = document.getElementById("tot-diff");
    diffEl.replaceChildren(h("span", { class: "chip " + (diff === 0n && t.dr > 0n ? "ok" : "bad") },
      diff === 0n ? (t.dr > 0n ? "✓ Balanced" : "Difference ₹0.00") : "Difference " + Money.exact(fixed(diff))));
    const desc = document.getElementById("adj-desc").value.trim();
    let hint = "";
    if (App.isLocked()) hint = "The engagement is locked after Partner sign-off.";
    else if (!canCreate()) hint = "Your role cannot create adjustments (Maker or Partner only).";
    else if (t.bad) hint = "Amounts must be numbers with at most 2 decimals.";
    else if (rows.length < 2) hint = "Add at least two lines.";
    else if (t.complete !== rows.length) hint = "Every line needs a ledger and either a debit or a credit (not both).";
    else if (diff !== 0n) hint = "Total debit must equal total credit.";
    else if (!desc) hint = "Enter a description.";
    document.getElementById("entry-hint").textContent = hint;
    document.getElementById("save-entry").disabled = !!hint || !canCreate();
  }

  function fixed(p) {                                 // BigInt paise -> "1234.50" (signed)
    const neg = p < 0n, a = neg ? -p : p;
    return (neg ? "-" : "") + (a / 100n) + "." + (a % 100n).toString().padStart(2, "0");
  }

  function addRow() { rows.push({ ledger: "", debit: "", credit: "", note: "" }); renderForm(); }

  function renderForm() {
    const tbody = document.querySelector("#adj-lines tbody");
    tbody.replaceChildren(...rows.map((r, i) => {
      const sel = h("select", { "aria-label": "Ledger for line " + (i + 1) }, h("option", { value: "" }, "— choose ledger —"),
        ...data.ledgers.map(l => h("option", { value: l.id }, l.name + (l.tally_group ? " (" + l.tally_group + ")" : ""))));
      sel.value = r.ledger;
      const mk = (key, cls) => h("input", { type: "text", inputmode: "decimal", class: cls, value: r[key], placeholder: "0.00", "aria-label": key + " line " + (i + 1) });
      const dr = mk("debit", "amt"), cr = mk("credit", "amt");
      const note = h("input", { type: "text", value: r.note, maxlength: 200, "aria-label": "Note line " + (i + 1) });
      sel.addEventListener("change", () => { r.ledger = sel.value; updateFooter(); });
      dr.addEventListener("input", () => { r.debit = dr.value; updateFooter(); });
      cr.addEventListener("input", () => { r.credit = cr.value; updateFooter(); });
      note.addEventListener("input", () => { r.note = note.value; });
      const rm = h("button", { type: "button", title: "Remove line", onclick: () => { rows.splice(i, 1); renderForm(); } }, "✕");
      return h("tr", {}, h("td", {}, sel), h("td", { class: "num" }, dr), h("td", { class: "num" }, cr), h("td", {}, note), h("td", {}, rm));
    }));
    updateFooter();
  }

  async function save() {
    const btn = document.getElementById("save-entry");
    btn.disabled = true;
    try {
      const res = await App.api("/api/engagements/" + eid() + "/adjustments", { method: "POST", body: {
        entry_date: document.getElementById("adj-date").value, description: document.getElementById("adj-desc").value,
        lines: rows.map(r => ({ ledger_id: Number(r.ledger), debit: r.debit.trim() || null, credit: r.credit.trim() || null, note: r.note })) } });
      App.toast("Adjustment " + res.entry_number + " saved as a draft.");
      rows.length = 0; document.getElementById("adj-desc").value = ""; addRow(); addRow();
      await loadAll();
    } catch (err) { App.toast(err.message, true); updateFooter(); }
  }

  // ---------- register ----------
  async function act(entry, verb) {
    try {
      await App.api("/api/engagements/" + eid() + "/adjustments/" + entry.id + "/" + verb, { method: "POST" });
      App.toast(verb === "approve" ? "Adjustment " + entry.entry_number + " approved. Statements and checks updated." : "Reversal drafted. It needs approval by someone else.");
      await loadAll(); await App.refreshHeader();
    } catch (err) { App.toast(err.message, true); }
  }

  function actions(e) {
    const me = App.state.user, out = [];
    if (e.status === "draft") {
      const self = me && e.created_by === me.id;
      const ok = canApprove() && !self;
      out.push(h("button", { type: "button", class: ok ? "primary" : "", disabled: !ok, onclick: () => act(e, "approve"),
        title: ok ? "Approve this adjustment" : self ? "You created this entry: someone else must approve it" : "Only a Checker or Partner can approve" }, "Approve"));
    } else if (e.reversed_by_entry_number) {
      out.push(h("span", { class: "muted" }, "Reversed by #" + e.reversed_by_entry_number));
    } else {
      out.push(h("button", { type: "button", disabled: !canCreate(), onclick: () => act(e, "reverse"),
        title: canCreate() ? "Create an opposite draft entry" : "Only a Maker or Partner can draft a reversal" }, "Reverse"));
    }
    return out;
  }

  function renderRegister() {
    document.getElementById("reg-counts").replaceChildren(
      h("span", { class: "chip warn" }, data.counts.draft + " draft"), " ", h("span", { class: "chip ok" }, data.counts.approved + " approved"));
    const tbody = document.querySelector("#register tbody");
    if (!data.entries.length) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 8, class: "muted" }, "No adjustments yet."))); return; }
    tbody.replaceChildren(...data.entries.map(e => h("tr", {},
      h("td", {}, "#" + e.entry_number), h("td", {}, e.entry_date),
      h("td", { class: "wrap" }, e.description, e.reverses_entry_number ? h("div", { class: "sub" }, "Reverses #" + e.reverses_entry_number) : null),
      h("td", { class: "wrap" }, ...e.lines.map(l => h("div", {}, (Number(l.debit) > 0 ? "Dr " : "Cr ") + l.ledger_name + "  ",
        h("b", {}, App.money(Number(l.debit) > 0 ? l.debit : l.credit))))),
      h("td", {}, h("span", { class: "chip " + (e.status === "approved" ? "ok" : "warn") }, e.status.toUpperCase())),
      h("td", {}, e.created_by_name), h("td", {}, e.approved_by_name || "—"), h("td", {}, ...actions(e)))));
  }

  // ---------- adjusted TB ----------
  function renderAtb() {
    document.getElementById("atb-unit").textContent = "Amounts in " + App.unitLabel();
    const only = document.getElementById("atb-changed").checked;
    const list = atb.rows.filter(r => !only || Number(r.adjustment_net) !== 0);
    document.querySelector("#atb tbody").replaceChildren(...list.map(r => h("tr", { class: Number(r.adjustment_net) !== 0 ? "changed" : "" },
      h("td", {}, r.ledger_name), h("td", {}, r.tally_group || ""), h("td", { class: "num" }, App.money(r.tb_balance)),
      h("td", { class: "num" }, App.money(r.adjustment_net)), h("td", { class: "num" }, App.money(r.adjusted_balance)))));
    const t = atb.totals;
    document.querySelector("#atb tfoot").replaceChildren(
      h("tr", { class: "total" }, h("td", { colspan: 2 }, "Total debit"), h("td", { class: "num" }, App.money(t.tb_debit)), h("td", {}), h("td", { class: "num" }, App.money(t.adjusted_debit))),
      h("tr", { class: "total" }, h("td", { colspan: 2 }, "Total credit"), h("td", { class: "num" }, App.money(t.tb_credit)), h("td", {}), h("td", { class: "num" }, App.money(t.adjusted_credit))),
      h("tr", {}, h("td", { colspan: 2 }, "Difference (must be nil)"), h("td", {}), h("td", { class: "num" }, Money.exact(t.adjustment_net)), h("td", { class: "num" }, Money.exact(t.adjusted_difference))));
  }

  async function loadAll() {
    [data, atb] = await Promise.all([App.api("/api/engagements/" + eid() + "/adjustments"), App.api("/api/engagements/" + eid() + "/adjusted-tb")]);
    renderRegister(); renderAtb(); renderForm();
  }

  App.ready.then(async () => {
    const d = new Date(); document.getElementById("adj-date").value = d.toISOString().slice(0, 10);
    document.getElementById("add-line").addEventListener("click", addRow);
    document.getElementById("save-entry").addEventListener("click", save);
    document.getElementById("adj-desc").addEventListener("input", updateFooter);
    document.getElementById("atb-changed").addEventListener("change", () => atb && renderAtb());
    App.onChange(what => { if (!data) return; if (what === "user") { renderRegister(); updateFooter(); } if (what === "unit") { renderRegister(); renderAtb(); } });
    await loadAll();
    addRow(); addRow();
  }).catch(err => App.toast(err.message, true));
})();
