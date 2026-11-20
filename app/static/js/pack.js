/* Audit Committee / IA Pack (T-16): five sections from GET /pack, print-ready, with a logged JSON snapshot download. */
(function () {
  const { h } = App;
  let data = null;
  const eid = () => App.state.engagement.id;
  const dl = pairs => h("dl", { class: "pack-grid" }, ...pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  const chip = (cls, text) => h("span", { class: "chip " + cls }, text);
  const fmtDate = s => (s ? new Date(s).toLocaleString() : "—");
  const table = (head, rows, numCols = []) => h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ...head.map((t, i) => h("th", { class: numCols.includes(i) ? "num" : "" }, t)))),
    h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: head.length, class: "muted" }, "None.")))));
  const tr = (cells, numCols = []) => h("tr", {}, ...cells.map((c, i) => h("td", { class: (numCols.includes(i) ? "num " : "") + "wrap" }, c)));
  const STATUS = { pass: ["ok", "PASS"], fail: ["bad", "FAIL"], not_run: ["warn", "OFF"] };

  function governance(hd) {
    const g = hd.governance, t = hd.template;
    return h("section", { class: "card pack-section" }, h("h2", {}, "1. Engagement & Governance"),
      dl([["Entity", hd.entity.name], ["CIN / PAN", [hd.entity.cin, hd.entity.pan].filter(Boolean).join(" / ") || "Not recorded"],
          ["Financial year", "FY " + hd.financial_year + " (" + hd.period_start + " to " + hd.period_end + ")"],
          ["Engagement", hd.engagement_type.replace(/_/g, " ").toLowerCase() + " · " + hd.engagement_status],
          ["Template", t.name + " · " + (t.confirmed ? "confirmed" : "NOT confirmed (Q2)")],
          ["Sign-off status", chip(g.is_locked ? "ok" : (g.can_sign_off ? "warn" : "bad"), (g.is_locked ? "🔒 " : "") + g.status)],
          ["Signed off", g.is_locked ? g.signed_off_by + ", " + fmtDate(g.signed_off_at) : "Not signed off (" + g.blockers + " blocker(s) open)"],
          ["Data revision / generated", hd.data_revision + " · " + fmtDate(hd.generated_at)]]),
      t.note ? h("div", { class: "banner", style: "margin:0" }, t.note) : null);
  }

  function financials(f) {
    const bs = f.balance_sheet, pl = f.profit_and_loss, v = bs.verification;
    const row = (x, bold) => tr([bold ? h("b", {}, x.label) : x.label, App.money(x.current), App.money(x.previous)], [1, 2]);
    return h("section", { class: "card pack-section" }, h("h2", {}, "2. Financial Highlights & Statements Summary"),
      h("div", { class: "pack-kpis" },
        kpi("Total assets", App.money(bs.total_assets.current)), kpi("Total equity & liabilities", App.money(bs.total_equity_and_liabilities.current)),
        kpi("Revenue from operations", App.money(pl.revenue_from_operations.current)), kpi("Profit / (loss) for the period", App.money(pl.profit_for_period.current))),
      h("p", {}, "Balance check: ", chip(v.status === "BALANCED" ? "ok" : "bad", v.status === "BALANCED" ? "✓ Balanced" : v.status),
        " assets − equity & liabilities = " + Money.exact(v.difference) + (f.unmapped_ledgers_excluded ? " · " + f.unmapped_ledgers_excluded + " unmapped ledger(s) excluded" : "")),
      h("p", { class: "muted" }, "Amounts in " + App.unitLabel()),
      table(["Balance Sheet", f.current_label, f.previous_label], [row(bs.shareholders_funds), row(bs.non_current_liabilities), row(bs.current_liabilities),
        row(bs.total_equity_and_liabilities, true), row(bs.non_current_assets), row(bs.current_assets), row(bs.total_assets, true)], [1, 2]),
      table(["Statement of Profit and Loss", f.current_label, f.previous_label], [row(pl.revenue_from_operations), row(pl.other_income), row(pl.total_income, true),
        row(pl.total_expenses), row(pl.profit_before_tax), row(pl.tax_expense), row(pl.profit_for_period, true)], [1, 2]),
      h("h3", {}, "Key ratios"), h("div", { class: "banner", style: "margin:0 0 8px" }, f.ratios.note + " (" + f.ratios.open_question + ")"),
      table(["Ratio", "Formula", "Value"], f.ratios.items.map(r => tr([r.name, r.formula,
        r.status === "na" ? "N/A" : r.value + (r.unit === "%" ? " %" : " times")], [2]))));
  }
  const kpi = (label, value) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));

  function adjustments(a) {
    const c = a.counts;
    return h("section", { class: "card pack-section" }, h("h2", {}, "3. Audit Adjustments"),
      h("div", { class: "row" }, chip("ok", c.approved + " approved"), chip("warn", c.draft + " draft"), chip("warn", c.reversed + " reversed"),
        h("span", { class: "muted" }, a.ledgers_with_net_adjustment + " ledger(s) with a net adjustment")),
      table(["#", "Date", "Description", "Status", "Created / approved by", "Amount"], a.entries.map(e => tr([
        "#" + e.entry_number, e.entry_date,
        e.description + (e.reverses_entry_number ? " (reverses #" + e.reverses_entry_number + ")" : "") + (e.reversed_by_entry_number ? " (reversed by #" + e.reversed_by_entry_number + ")" : ""),
        e.status.toUpperCase(), e.created_by + " / " + (e.approved_by || "—"), App.money(e.total)], [5]))));
  }

  function checksAndIssues(c) {
    const s = c.checks_summary, i = c.issues;
    return h("section", { class: "card pack-section" }, h("h2", {}, "4. Checks & Open Exceptions"),
      h("div", { class: "row" }, chip(s.passed === s.total ? "ok" : "bad", s.text || "Checks not run"), chip(i.open ? "bad" : "ok", i.open + " open issue(s)"),
        h("span", { class: "muted" }, (s.notes || []).join("; "))),
      table(["Check", "Name", "Status", "Difference"], c.checks.map(k => {
        const [cls, label] = STATUS[k.status] || ["warn", k.status.toUpperCase()];
        return tr([k.code, k.name + (k.message ? " – " + k.message : ""), chip(cls, label),
          k.status === "not_run" || k.variance === null ? "—" : (k.count_based ? String(Number(k.variance)) : Money.exact(k.variance))], [3]);
      }), [3]),
      h("h3", {}, "Open issues (" + i.open + ")"),
      table(["#", "Source", "Issue", "Amount"], i.open_items.map(x => tr(["#" + x.id, x.source.toUpperCase(), x.title + (x.detail ? " – " + x.detail : ""),
        x.amount === null ? "" : App.money(x.amount)], [3]))));
  }

  function workingPapers(w) {
    const l = w.statement_lines, n = w.notes;
    const un = w.uncovered;
    return h("section", { class: "card pack-section" }, h("h2", {}, "5. Working Paper Coverage"),
      h("div", { class: "row" }, chip(l.covered === l.total ? "ok" : "warn", "Statement lines: " + l.covered + " of " + l.total + " covered"),
        chip(n.covered === n.total ? "ok" : "warn", "Notes: " + n.covered + " of " + n.total + " covered"),
        h("span", { class: "muted" }, w.reviewed_links + " of " + w.total_links + " papers reviewed · " + l.fully_reviewed + " lines and " + n.fully_reviewed + " notes fully reviewed")),
      un.length ? h("details", {}, h("summary", {}, un.length + " line(s)/note(s) without a working paper"),
        h("ul", {}, ...un.map(u => h("li", {}, (u.target_type === "note" ? "Note: " : "Line: ") + u.label)))) : h("p", {}, "Every statement line and note has a working paper."));
  }

  function renderStale() {
    const box = document.getElementById("pack-stale");
    if (!data.is_stale) {
      box.replaceChildren(h("div", { class: "chip ok" }, "✓ Up to date: built at revision " + data.last_built_revision + (data.last_built_by ? " by " + data.last_built_by : "")));
      return;
    }
    const never = data.last_built_revision === null;
    box.replaceChildren(h("div", { class: "banner", style: "margin:0 0 12px", role: "alert" },
      h("b", {}, never ? "⚠ This pack has not been built yet. " : "⚠ This pack is out of date. "),
      never ? "Rebuild it to record the revision you are about to present." : "The statements or registers changed after it was last built (revision " + data.last_built_revision + ", now " + data.current_revision + ").",
      " ", h("button", { type: "button", class: "primary", onclick: rebuild }, "Rebuild / Refresh Snapshot")));
  }

  function render() {
    renderStale();
    document.getElementById("pack").replaceChildren(governance(data.header), financials(data.financials), adjustments(data.adjustments),
      checksAndIssues(data.checks_and_issues), workingPapers(data.working_papers));
  }

  async function load() { data = await App.api("/api/engagements/" + eid() + "/pack"); render(); }
  const logExport = format => App.api("/api/engagements/" + eid() + "/pack/export", { method: "POST", body: { format } });

  async function rebuild() {
    try { await App.api("/api/engagements/" + eid() + "/pack/build", { method: "POST" }); await load(); App.toast("Snapshot rebuilt at revision " + data.current_revision + "."); } catch (err) { App.toast(err.message, true); }
  }

  async function printPack() {
    try { await load(); await logExport("print"); await load(); window.print(); } catch (err) { App.toast(err.message, true); }
  }

  async function downloadJson() {
    try {
      await load(); await logExport("json"); await load();
      const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), pack: data }, null, 2)], { type: "application/json" });
      const a = h("a", { href: URL.createObjectURL(blob), download: "audit-pack-FY" + data.header.financial_year + ".json" });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      App.toast("Snapshot downloaded and logged.");
    } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    document.getElementById("pack-print").addEventListener("click", printPack);
    document.getElementById("pack-json").addEventListener("click", downloadJson);
    document.getElementById("pack-build").addEventListener("click", rebuild);
    App.onChange(what => { if (data && what === "unit") render(); });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
