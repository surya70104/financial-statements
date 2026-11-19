/* Ratios tab (T-10). */
(function () {
  const { h } = App;
  let data = null;

  function value(r) {
    if (r.status === "na") return h("span", { class: "chip warn", title: r.explanation }, "N/A");
    return document.createTextNode(r.value + (r.unit === "%" ? " %" : " times"));      // exact 2-decimal string from the server
  }

  function render() {
    document.getElementById("ratios-unit").textContent = "Numerator and denominator in " + App.unitLabel();
    const b = document.getElementById("q7-banner");
    b.hidden = !data.provisional; b.textContent = data.provisional_message;
    const w = data.warnings, box = document.getElementById("stmt-warning");
    box.hidden = !w.unmapped_ledgers;
    if (w.unmapped_ledgers) box.replaceChildren(h("b", {}, "⚠ " + w.unmapped_ledgers + " ledger(s) not mapped and excluded."), " ", h("a", { href: "/mapping" }, "Go to Mapping"));
    document.querySelector("#ratios-table tbody").replaceChildren(...data.items.flatMap(r => {
      const rows = [h("tr", {},
        h("td", {}, r.name, h("div", { class: "sub" }, r.category + (r.is_provisional ? " · provisional" : ""))),
        h("td", { class: "wrap" }, r.formula),
        h("td", { class: "num", title: r.numerator_label }, App.money(r.numerator), h("div", { class: "sub" }, r.numerator_label)),
        h("td", { class: "num", title: r.denominator_label }, App.money(r.denominator), h("div", { class: "sub" }, r.denominator_label)),
        h("td", { class: "num" }, value(r)))];
      const notes = [r.explanation, r.note].filter(Boolean).join(" ");
      if (notes) rows.push(h("tr", { class: "extra" }, h("td", { colspan: 5, class: "wrap sub" }, notes)));
      return rows;
    }));
  }

  App.ready.then(async () => {
    App.onChange(what => { if (data && what === "unit") render(); });
    data = await App.api("/api/engagements/" + App.state.engagement.id + "/ratios");
    render();
  }).catch(err => App.toast(err.message, true));
})();
