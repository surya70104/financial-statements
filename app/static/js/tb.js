/* Trial Balance & Import screen */
(function () {
  const { h } = App;
  let data = null;

  const fmtDate = s => { const d = new Date(s); return isNaN(d) ? s : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }); };
  const eid = () => App.state.engagement.id;

  async function load() {
    data = await App.api("/api/engagements/" + eid() + "/tb");
    render();
  }

  function stat(label, value, hint, cls) {
    return h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value " + (cls || "") }, value), hint ? h("div", { class: "hint" }, hint) : null);
  }

  function renderSummary() {
    const s = data.summary;
    const foots = Number(s.difference) === 0 && s.ledger_count > 0;
    document.getElementById("summary-cards").replaceChildren(
      stat("Active ledgers", String(s.ledger_count), s.inactive_ledgers ? s.inactive_ledgers + " inactive (dropped from latest file)" : "from latest import"),
      stat("Total debits", App.money(s.total_debit), App.unitLabel()),
      stat("Total credits", App.money(s.total_credit), foots ? "✓ trial balance foots" : "difference " + App.money(s.difference)),
      stat("Assets (by Tally group)", App.money(s.net_assets_by_tally_group), "before Schedule III mapping"),
      stat("Net result (by Tally group)", App.money(s.net_result_by_tally_group), Number(s.net_result_by_tally_group) < 0 ? "loss for the period" : "profit for the period"),
      stat("Unmapped ledgers", String(s.unmapped_ledgers), s.open_mapping_exceptions + " open mapping exception(s)" + (s.matches ? " ✓ match" : " ⚠ mismatch"))
    );
  }

  function renderLedgers() {
    const q = document.getElementById("ledger-filter").value.trim().toLowerCase();
    const rows = data.ledgers.filter(r => !q || (r.name + " " + (r.tally_group || "")).toLowerCase().includes(q));
    document.getElementById("ledger-count").textContent = "(" + rows.length + " of " + data.ledgers.length + ")";
    const unit = App.unitLabel();
    document.querySelectorAll("#ledger-table th.num").forEach(th => { th.dataset.base = th.dataset.base || th.textContent; th.textContent = th.dataset.base + " (" + unit + ")"; });
    document.getElementById("unit-note").textContent = "Amounts are stored in exact rupees and shown in " + (App.state.unit === "LAKH" ? "lakhs" : "rupees") + "; negative = credit balance.";
    document.querySelector("#ledger-table tbody").replaceChildren(...rows.map((r, i) =>
      h("tr", { class: r.is_active ? "" : "inactive" },
        h("td", {}, i + 1), h("td", {}, r.name), h("td", {}, r.tally_group || ""),
        h("td", { class: "num" }, App.money(r.opening_balance)),
        h("td", { class: "num" }, App.money(r.period_debit)),
        h("td", { class: "num" }, App.money(r.period_credit)),
        h("td", { class: "num" }, App.money(r.closing_balance)),
        h("td", {}, !r.is_active ? h("span", { class: "chip warn" }, "inactive") :
          r.has_mapping ? h("span", { class: "chip ok" }, "mapped") : h("span", { class: "chip bad" }, "unmapped")))));
  }

  function renderUploads() {
    const unit = App.unitLabel();
    document.querySelector("#upload-table tbody").replaceChildren(...data.uploads.map(u =>
      h("tr", {},
        h("td", {}, fmtDate(u.uploaded_at)), h("td", {}, u.source_file_name), h("td", {}, u.uploaded_by),
        h("td", {}, h("span", { class: "chip " + (u.status === "committed" ? "ok" : "bad") }, u.status)),
        h("td", { class: "num" }, u.rows_total), h("td", { class: "num" }, u.ledgers_inserted), h("td", { class: "num" }, u.ledgers_updated),
        h("td", { class: "num" }, u.ledgers_unchanged), h("td", { class: "num" }, u.ledgers_deactivated),
        h("td", { class: "num", title: unit }, u.total_debit === null ? "" : App.money(u.total_debit)),
        h("td", { class: "num", title: unit }, u.total_credit === null ? "" : App.money(u.total_credit)),
        h("td", {}, u.message || ""))));
    if (!data.uploads.length) document.querySelector("#upload-table tbody").append(h("tr", {}, h("td", { colspan: 12, class: "muted" }, "No imports yet.")));
  }

  function render() { renderSummary(); renderLedgers(); renderUploads(); }

  function showResult(ok, title, body) {
    const box = document.getElementById("import-result");
    box.hidden = false; box.className = "panel " + (ok ? "ok" : "bad");
    box.replaceChildren(h("b", {}, title), body || "");
  }

  async function doImport(ev) {
    ev.preventDefault();
    const file = document.getElementById("import-file").files[0];
    if (!file) return;
    const btn = document.getElementById("import-btn");
    btn.disabled = true; document.getElementById("import-hint").textContent = "Importing…";
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await App.api("/api/engagements/" + eid() + "/tb/import", { method: "POST", body: fd });
      const parts = [r.inserted + " new", r.updated + " updated", r.unchanged + " unchanged", r.deactivated + " deactivated"];
      const note = r.deactivated ? " Dropped ledgers keep their saved mappings: " + r.deactivated_ledgers.join(", ") + "." : "";
      showResult(true, "Imported " + r.file + ": " + parts.join(", ") + ".",
        h("div", {}, "Unmapped ledgers: " + r.exceptions.unmapped_ledgers + "; open mapping exceptions: " + r.exceptions.open_mapping_exceptions + "." + (r.changed ? "" : " Nothing changed in this file.") + note));
      await Promise.all([load(), App.refreshHeader()]);
    } catch (err) {
      const errors = (err.detail && err.detail.errors) || [];
      const body = errors.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Row"), h("th", {}, "Ledger"), h("th", {}, "Problem"))),
        h("tbody", {}, ...errors.slice(0, 50).map(e => h("tr", {}, h("td", {}, e.row || ""), h("td", {}, e.ledger), h("td", {}, e.message))))) : "";
      showResult(false, err.message, body);
      load().catch(() => {});                       // the failed attempt appears in Upload history
    } finally {
      btn.disabled = !App.canEdit(); document.getElementById("import-hint").textContent = "";
    }
  }

  function applyRole() {
    const ok = App.canEdit();
    document.getElementById("import-btn").disabled = !ok;
    document.getElementById("import-file").disabled = !ok;
    document.getElementById("import-hint").textContent = ok ? "" : "Your role is read-only. Switch to Maker or Partner to import.";
  }

  App.ready.then(() => {
    document.getElementById("import-form").addEventListener("submit", doImport);
    document.getElementById("ledger-filter").addEventListener("input", renderLedgers);
    App.onChange(what => { if (what === "unit") render(); if (what === "user") applyRole(); });
    applyRole();
    return load();
  }).catch(err => App.toast(err.message, true));
})();
