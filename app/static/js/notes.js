/* Notes to the financial statements (T-09): one expandable card per note, with a live tie-out to its statement line. */
(function () {
  const { h } = App;
  let data = null, allOpen = false;
  const openNotes = new Set();

  function noteCard(n) {
    const rows = n.ledgers.map(l => h("tr", {}, h("td", {}, l.ledger_name), h("td", {}, l.tally_group || ""),
      h("td", { class: "num" }, App.money(l.opening)), h("td", { class: "num" }, App.money(l.movement)), h("td", { class: "num" }, App.money(l.closing))));
    n.extras.forEach(e => rows.push(h("tr", { class: "extra" }, h("td", { colspan: 4 }, e.label), h("td", { class: "num" }, App.money(e.closing)))));
    if (!rows.length) rows.push(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "No ledgers are mapped to this note.")));
    const tie = n.ties
      ? h("span", { class: "chip ok" }, "✓ Ties to Statement")
      : h("span", { class: "chip bad" }, "✗ Differs by " + Money.exact(n.difference));
    const d = h("details", { class: "card note-card" },
      h("summary", {}, h("span", { class: "note-title" }, "Note " + n.note_no + " – " + n.title),
        h("span", { class: "note-total" }, App.money(n.note_total)), WP.chip("note", "NOTE-" + n.note_no, "Note " + n.note_no + " – " + n.title), tie),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Ledger"), h("th", {}, "Tally group"), h("th", { class: "num" }, "Opening"),
          h("th", { class: "num" }, "Movement"), h("th", { class: "num" }, "Closing " + data.current_label))),
        h("tbody", {}, rows),
        h("tfoot", {},
          h("tr", { class: "total" }, h("td", { colspan: 4 }, "Note " + n.note_no + " total"), h("td", { class: "num" }, App.money(n.note_total))),
          h("tr", {}, h("td", { colspan: 4 }, "Statement line: " + n.line_label), h("td", { class: "num" }, App.money(n.line_total))),
          h("tr", {}, h("td", { colspan: 4 }, "Difference"), h("td", { class: "num" }, Money.exact(n.difference)))))));
    d.open = allOpen || openNotes.has(n.note_no) || !n.ties;
    d.addEventListener("toggle", () => { if (d.open) openNotes.add(n.note_no); else openNotes.delete(n.note_no); });
    return d;
  }

  function fdNote(fd) {
    const el = document.getElementById("fd-note");
    const dmy = s => s.split("-").reverse().join("-");
    const rows = fd.rows.map(r => h("tr", { class: r.status === "posted" ? "" : "muted" },
      h("td", {}, r.bank_name), h("td", {}, r.fd_number), h("td", {}, r.maturity_classification === "current" ? "Current" : "Non-current"),
      h("td", { class: "num" }, r.maturity_classification === "current" ? App.money(r.principal_amount) : "—"),
      h("td", { class: "num" }, r.maturity_classification === "non_current" ? App.money(r.principal_amount) : "—"),
      h("td", { class: "num" }, r.interest_rate + " %"), h("td", {}, dmy(r.maturity_date)), h("td", { class: "num" }, App.money(r.accrued_interest)),
      h("td", {}, r.status === "posted" ? "Posted" : "Draft: not in the statements")));
    const t = fd.totals;
    el.replaceChildren(h("h2", {}, "Supporting schedule: Fixed deposits and accrued interest"),
      h("p", { class: "muted" }, "Read from the FD register. Totals count posted FDs only; they are the FDs inside the statements. Current FDs sit on the line chosen in the register (usually Cash and cash equivalents), non-current on Other non-current assets, and accrued interest on Other current assets with the income in Other income."),
      fd.rows.length ? h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["Bank", "FD number", "Class", "Current principal", "Non-current principal", "Rate", "Maturity", "Accrued interest", "Status"].map((t_, i) => h("th", { class: [3, 4, 7].includes(i) ? "num" : "" }, t_)))),
        h("tbody", {}, rows),
        h("tfoot", {}, h("tr", { class: "total" }, h("td", { colspan: 3 }, "Total (posted FDs)"), h("td", { class: "num" }, App.money(t.current_principal)),
          h("td", { class: "num" }, App.money(t.non_current_principal)), h("td", {}), h("td", {}), h("td", { class: "num" }, App.money(t.accrued_interest)), h("td", {}, App.money(t.total_principal) + " principal")))))
        : h("p", { class: "muted" }, "No fixed deposits in the register. Add them on the FD Register tab."));
  }

  function render() {
    document.getElementById("notes-unit").textContent = "Amounts in " + App.unitLabel();
    const t = data.tie_summary;
    document.getElementById("notes-summary").replaceChildren(h("span", { class: "chip " + (t.failing.length ? "bad" : "ok") },
      t.tying + " of " + t.notes + " notes tie to their statement line"));
    const w = data.warnings, box = document.getElementById("stmt-warning");
    box.hidden = !w.unmapped_ledgers;
    if (w.unmapped_ledgers) box.replaceChildren(h("b", {}, "⚠ " + w.unmapped_ledgers + " ledger(s) not mapped and excluded."), " ", h("a", { href: "/mapping" }, "Go to Mapping"));
    document.getElementById("notes-list").replaceChildren(...data.notes.map(noteCard));
    fdNote(data.fd_note);
  }

  App.ready.then(async () => {
    document.getElementById("notes-toggle").addEventListener("click", e => {
      allOpen = !allOpen; e.target.textContent = allOpen ? "Collapse all" : "Expand all"; openNotes.clear(); render();
    });
    App.onChange(what => { if (data && what === "unit") render(); });
    data = await App.api("/api/engagements/" + App.state.engagement.id + "/notes");
    WP.subscribe(() => { if (data) render(); });
    await WP.load();                                       // renders via the subscription above
  }).catch(err => App.toast(err.message, true));
})();
