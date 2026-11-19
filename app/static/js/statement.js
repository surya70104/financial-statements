/* Schedule III Balance Sheet and Profit & Loss: one renderer, two endpoints (page decides which). */
(function () {
  const { h } = App;
  const page = document.body.dataset.page;               // "bs" | "pnl"
  const endpoint = page === "bs" ? "balance-sheet" : "pnl";
  let data = null;

  function lineRow(l, topLevel) {
    const total = l.kind !== "mapped";
    const tr = h("tr", { class: total ? "total" : "" },
      h("td", { class: total ? "" : "indent" }, l.label, total ? null : WP.chip("statement_line", l.code, l.label)),
      h("td", {}, l.note_no || ""),
      h("td", { class: "num" }, App.money(l.current)),
      h("td", { class: "num" }, App.money(l.previous)));
    if (l.breakdown) {
      const b = l.breakdown;
      tr.querySelector("td").append(h("div", { class: "sub" },
        "TB reserves " + App.money(b.per_trial_balance) + " + profit/(loss) for the period " + App.money(b.profit_for_period) +
        " + other movements " + App.money(b.other_movements)));
    }
    return tr;
  }

  function sectionRows(sections) {
    return sections.flatMap(s => [h("tr", { class: "section" }, h("td", { colspan: 4 }, s.name)), ...s.lines.map(l => lineRow(l))]);
  }

  function render() {
    document.getElementById("th-cur").textContent = data.current_label;
    document.getElementById("th-prev").textContent = data.previous_label;
    document.getElementById("stmt-unit").textContent = "Amounts in " + App.unitLabel();
    const rows = page === "bs"
      ? data.parts.flatMap(p => [h("tr", { class: "part" }, h("td", { colspan: 4 }, p.name)), ...sectionRows(p.sections)])
      : sectionRows(data.sections);
    document.querySelector("#stmt-table tbody").replaceChildren(...rows);

    const w = data.warnings, box = document.getElementById("stmt-warning");
    box.hidden = !w.unmapped_ledgers;
    if (w.unmapped_ledgers) {
      box.replaceChildren(
        h("b", {}, "⚠ " + w.unmapped_ledgers + " ledger" + (w.unmapped_ledgers === 1 ? " is" : "s are") + " not mapped and excluded from this statement."),
        " Net balance left out: " + App.money(w.unmapped_net_balance) + ". ",
        h("a", { href: "/mapping" }, "Go to Mapping"));
    }

    const chk = document.getElementById("stmt-check");
    if (page === "bs") {
      const c = data.check;
      const cls = c.status === "BALANCED" ? "ok" : c.status === "INCOMPLETE" ? "warn" : "bad";
      const text = c.status === "BALANCED" ? "✓ Balanced: assets = equity and liabilities"
        : "Difference (assets − equity & liabilities): " + Money.exact(c.difference);
      chk.replaceChildren(h("span", { class: "chip " + cls }, text));
    } else {
      chk.replaceChildren(h("span", { class: "chip " + (data.warnings.unmapped_ledgers ? "warn" : "ok") },
        "Profit / (loss) for the period: " + App.money(data.profit_for_period.current)));
    }
  }

  async function load() {
    data = await App.api("/api/engagements/" + App.state.engagement.id + "/statements/" + endpoint);
    WP.subscribe(() => { if (data) render(); });           // re-draw the chips whenever working papers change
    await WP.load();                                       // renders via the subscription above
  }

  App.ready.then(() => { App.onChange(what => { if (data && what === "unit") render(); }); return load(); })
    .catch(err => App.toast(err.message, true));
})();
