/* Materiality (T-23): manual or calculated, live preview in exact paise (BigInt), and the lines above materiality. */
(function () {
  const { h } = App;
  let data = null;
  const eid = () => App.state.engagement.id;
  const $ = id => document.getElementById(id);
  const mode = () => document.querySelector("input[name=mat-mode]:checked").value;
  const canSet = () => !!App.state.user && App.state.user.roles.some(r => r === "CHECKER" || r === "PARTNER");
  const AMT = /^\d{1,16}(\.\d{1,2})?$/, PCT = /^\d{1,3}(\.\d{1,2})?$/;
  const stat = (label, value, hint) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);
  const clean = s => s.trim().replace(/,/g, "");
  const half = (num, den) => (num + den / 2n) / den;                      // half-up for non-negative BigInt

  /* What the server will compute, in paise. Returns null while the inputs are incomplete or invalid. */
  function preview() {
    let overall = null;
    if (mode() === "manual") {
      const v = clean($("m-overall").value);
      if (AMT.test(v) && Money.toPaise(v) > 0n) overall = Money.toPaise(v);
    } else {
      const pct = clean($("m-pct").value), b = data.benchmarks[$("m-basis").value];
      if (b && PCT.test(pct) && Money.toPaise(pct) > 0n && Money.toPaise(pct) <= 10000n && Money.toPaise(b.amount) > 0n) {
        overall = half(Money.toPaise(b.amount) * Money.toPaise(pct), 10000n);       // benchmark x % / 100, exact
        if (overall === 0n) overall = null;
      }
    }
    if (overall === null) return null;
    const optional = id => { const v = clean($(id).value); return v && AMT.test(v) && Money.toPaise(v) > 0n ? Money.toPaise(v) : null; };
    return { overall, perf: optional("m-perf") ?? half(overall * 75n, 100n), triv: optional("m-triv") ?? half(overall * 5n, 100n) };
  }
  const paiseStr = p => (p / 100n).toString() + "." + (p % 100n).toString().padStart(2, "0");

  function renderCurrent() {
    const m = data.materiality;
    $("mat-status").replaceChildren(m ? h("span", { class: "chip ok" }, "Set by " + m.set_by) : h("span", { class: "chip warn" }, "Not set yet"));
    $("mat-current").replaceChildren(...(m ? [h("div", { class: "pack-kpis" },
      stat("Overall materiality", Money.exact(m.overall_materiality), m.input_mode === "manual" ? "Entered manually" : m.benchmark_pct + "% of " + data.benchmarks[m.benchmark_basis].label.toLowerCase() + " " + Money.exact(m.benchmark_amount)),
      stat("Performance materiality", Money.exact(m.performance_materiality)), stat("Clearly trivial", Money.exact(m.trivial_threshold)),
      stat("Set", new Date(m.set_at).toLocaleDateString(), m.set_by + (m.notes ? " · " + m.notes : "")))] : []));
  }

  function renderForm() {
    const calc = mode() === "calculated";
    $("mat-manual").hidden = calc; $("mat-calc").hidden = !calc;
    const b = data.benchmarks[$("m-basis").value];
    $("m-bench").textContent = b ? b.label + " now: " + Money.exact(b.amount) + " (read from the statements)" : "";
    const p = preview(), locked = App.state.engagement.is_locked;
    $("mat-preview").replaceChildren(...(p ? [stat("Overall materiality", Money.exact(paiseStr(p.overall))), stat("Performance materiality", Money.exact(paiseStr(p.perf)), "75% unless entered"),
      stat("Clearly trivial", Money.exact(paiseStr(p.triv)), "5% unless entered")] : []));
    let why = "";
    if (locked) why = "The engagement is signed off and locked. A Partner must re-open it from the Sign-Off tab before materiality can change.";
    else if (!canSet()) why = "Only a Checker or Partner can set materiality (you are " + App.state.user.roles.join(", ") + ").";
    else if (!p) why = calc ? "Choose a benchmark with a positive amount and enter a percentage between 0.01 and 100." : "Enter an overall materiality amount (up to 2 decimals).";
    else if (p.perf > p.overall || p.triv > p.overall) why = "Performance materiality and the trivial threshold cannot exceed overall materiality.";
    $("m-save").disabled = !!why; $("m-hint").textContent = why;
    renderTable(p); renderDifferences(p);
  }

  function renderTable(p) {
    const m = data.materiality;
    const threshold = p ? p.overall : (m ? Money.toPaise(m.overall_materiality) : null);
    const rows = threshold === null ? [] : data.variances.filter(v => Money.toPaise(v.variance) > threshold);
    $("mat-count").textContent = threshold === null ? "" : rows.length + " line(s) above " + Money.exact(paiseStr(threshold)) + (p ? " (preview)" : "");
    $("mat-note").textContent = !data.comparatives_available ? "No prior-year comparatives are loaded, so year-on-year variances cannot be assessed yet."
      : threshold === null ? "Set materiality to see which lines exceed it." : "";
    $("mat-table").querySelector("tbody").replaceChildren(...(rows.length ? rows.map(v => h("tr", {},
      h("td", {}, v.label), h("td", {}, v.note_no ? "Note " + v.note_no : "—"), h("td", { class: "num" }, App.money(v.current)), h("td", { class: "num" }, App.money(v.previous)),
      h("td", { class: "num" }, App.money(v.change)), h("td", { class: "num" }, Money.exact(paiseStr(Money.toPaise(v.variance) - threshold)))))
      : [h("tr", {}, h("td", { colspan: 6, class: "muted" }, threshold === null ? "—" : "No line is above materiality."))]));
  }

  function renderDifferences(p) {
    const m = data.materiality, threshold = p ? p.overall : (m ? Money.toPaise(m.overall_materiality) : null);
    const rows = threshold === null ? [] : data.differences.filter(d => Money.toPaise(d.variance) > threshold);
    $("dif-count").textContent = threshold === null ? data.differences.length + " open difference(s)" : rows.length + " of " + data.differences.length + " above " + Money.exact(paiseStr(threshold)) + (p ? " (preview)" : "");
    $("dif-table").querySelector("tbody").replaceChildren(...(rows.length ? rows.map(d => h("tr", {},
      h("td", {}, h("span", { class: "chip " + (d.source === "gstr9c" ? "bad" : "warn") }, d.source === "gstr9c" ? "GSTR-9C" : "RECONCILIATION")),
      h("td", { class: "wrap" }, d.label), h("td", { class: "num" }, Money.exact(d.difference)), h("td", { class: "num" }, Money.exact(paiseStr(Money.toPaise(d.variance) - threshold)))))
      : [h("tr", {}, h("td", { colspan: 4, class: "muted" }, threshold === null ? "Set materiality to see which differences exceed it." : "No open difference is above materiality."))]));
  }

  async function load() {
    data = await App.api("/api/engagements/" + eid() + "/materiality");
    const sel = $("m-basis"), keep = sel.value;
    sel.replaceChildren(...Object.entries(data.benchmarks).map(([k, b]) => h("option", { value: k }, b.label)));
    if (keep) sel.value = keep;
    renderCurrent(); renderForm();
  }

  async function save() {
    const btn = $("m-save"); btn.disabled = true;
    const body = { input_mode: mode(), notes: $("m-notes").value, performance_materiality: clean($("m-perf").value) || null, trivial_threshold: clean($("m-triv").value) || null };
    if (mode() === "manual") body.overall_materiality = clean($("m-overall").value);
    else { body.benchmark_basis = $("m-basis").value; body.benchmark_pct = clean($("m-pct").value); }
    try {
      data = await App.api("/api/engagements/" + eid() + "/materiality", { method: "POST", body });
      await App.refreshHeader(); App.toast("Materiality saved. The Issue Tracker has been updated.");
      renderCurrent(); renderForm();
    } catch (err) { App.toast(err.message, true); renderForm(); }
  }

  App.ready.then(async () => {
    document.querySelectorAll("input[name=mat-mode]").forEach(r => r.addEventListener("change", () => data && renderForm()));
    ["m-overall", "m-pct", "m-perf", "m-triv", "m-basis"].forEach(id => $(id).addEventListener("input", () => data && renderForm()));
    $("m-save").addEventListener("click", save);
    App.onChange(what => { if (!data) return; if (what === "user") renderForm(); if (what === "unit") { renderCurrent(); renderTable(preview()); } });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
