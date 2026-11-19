/* Checks dashboard (T-11). */
(function () {
  const { h } = App;
  let data = null;
  const eid = () => App.state.engagement.id;
  const BADGE = { pass: ["ok", "PASS"], fail: ["bad", "FAIL"], not_run: ["warn", "OFF"] };

  function difference(it) {
    if (it.status === "not_run" || it.status === null) return "—";
    if (it.variance === null) return "n/a";
    return it.detail && it.detail.unit === "count" ? String(Number(it.variance)) : Money.exact(it.variance);   // exact paise: never rounded away in Lakhs mode
  }

  function detailText(it) {
    const d = it.detail || {};
    if (d.message) return d.message;
    if (it.code === "CHK-03" && d.failing_notes && d.failing_notes.length)
      return d.failing_notes.map(f => "Note " + f.note_no + " differs by " + Money.exact(f.difference)).join("; ");
    if (it.code === "CHK-03" && d.notes_checked !== undefined) return d.notes_tying + " of " + d.notes_checked + " notes tie to their statement line";
    if (it.code === "CHK-04" && d.unmapped_ledgers !== undefined)
      return d.unmapped_ledgers + " unmapped ledger(s), net balance " + App.money(d.unmapped_net_balance);
    if (it.code === "CHK-05" && d.unit === "count") return Number(it.rhs_amount) + " of " + Number(it.lhs_amount) + " lines have a comparative";
    if (it.lhs_amount !== null && it.rhs_amount !== null && d.unit !== "count")
      return (d.labels ? d.labels[0] : "Left") + " " + App.money(it.lhs_amount) + " vs " + (d.labels ? d.labels[1] : "Right") + " " + App.money(it.rhs_amount);
    return "";
  }

  function render() {
    const s = data.summary;
    const banner = document.getElementById("checks-banner");
    if (!s.has_run) {
      banner.className = "panel bad"; banner.textContent = "Checks have not been run yet.";
    } else {
      const all = s.passed === s.total;
      banner.className = "panel " + (all ? "ok" : "bad");
      banner.replaceChildren(h("b", {}, (all ? "✓ " : "⚠ ") + s.text + "."),
        s.notes.length ? " " + s.notes.join("; ") + "." : "", s.stale ? " Results are out of date: re-run." : "");
    }
    document.getElementById("checks-run-info").textContent = s.has_run ? "Last run " + new Date(s.run_at).toLocaleString() : "";
    document.querySelector("#checks-table tbody").replaceChildren(...data.items.map(it => {
      const [cls, label] = BADGE[it.status] || ["warn", "NOT RUN"];
      return h("tr", {}, h("td", {}, it.code), h("td", {}, it.name), h("td", { class: "wrap" }, it.description),
        h("td", {}, h("span", { class: "chip " + cls }, label)), h("td", { class: "num" }, difference(it)), h("td", { class: "wrap" }, detailText(it)));
    }));
    App.setChecksBadge(s);
  }

  async function load() { data = await App.api("/api/engagements/" + eid() + "/checks"); render(); }

  async function rerun(quiet) {
    const btn = document.getElementById("rerun");
    btn.disabled = true;
    try {
      data = await App.api("/api/engagements/" + eid() + "/checks/run", { method: "POST" }); render();
      if (!quiet) App.toast("Checks re-run: " + data.summary.text + ".");
    } catch (err) { App.toast(err.message, true); }
    finally { btn.disabled = false; }
  }

  App.ready.then(async () => {
    document.getElementById("rerun").addEventListener("click", () => rerun(false));
    App.onChange(what => { if (data && what === "unit") render(); });
    await load();
    if (!data.summary.has_run || data.summary.stale) await rerun(true);   // first visit / out of date: bring it up to date
  }).catch(err => App.toast(err.message, true));
})();
