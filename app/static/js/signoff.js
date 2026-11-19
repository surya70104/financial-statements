/* Sign-off gate (T-13): readiness card, Partner-only sign-off form, locked certificate. */
(function () {
  const { h } = App;
  let data = null;
  const eid = () => App.state.engagement.id;
  const isPartner = () => !!App.state.user && App.state.user.roles.includes("PARTNER");

  function blockerRow(b) {
    if (b.type === "ISSUE") {
      return h("div", { class: "blocker" }, h("span", { class: "chip bad" }, "ISSUE · " + b.source.toUpperCase()),
        h("div", {}, h("b", {}, b.title), b.message ? h("div", { class: "why" }, b.message) : null,
          h("div", { class: "why" }, "Open issue: ", h("a", { href: "/issues" }, "go to Issues"))));
    }
    if (b.type === "CHECKS_NOT_RUN") return h("div", { class: "blocker" }, h("span", { class: "chip bad" }, "CHECKS"), h("div", {}, h("b", {}, b.title)));
    const why = b.pending_definition ? b.message + ". This check counts as not passing until the Product Owner answers Q1." : b.message;
    return h("div", { class: "blocker" }, h("span", { class: "chip bad" }, "CHECK · " + (b.status === "not_run" ? "NOT RUN" : "FAIL")),
      h("div", {}, h("b", {}, b.title), h("div", { class: "why" }, why + (b.variance !== null && !b.count_based && !b.pending_definition ? " (difference " + Money.exact(b.variance) + ")" : ""))));
  }

  const vBadge = v => h("span", { class: "chip " + (v && v.status === "signed_locked" ? "ok" : "warn") }, v ? "v" + v.version_number : "—");

  function renderGate() {
    const st = document.getElementById("gate-status"), box = document.getElementById("blockers");
    if (data.is_locked) {
      st.replaceChildren(vBadge(data.active_version), " ", h("span", { class: "chip ok" }, "🔒 Signed off and locked"));
      box.replaceChildren(h("p", { class: "muted" }, "Version v" + data.active_version.version_number + " is signed off. No further changes are accepted unless the Partner re-opens the engagement."));
      return;
    }
    const next = h("span", { class: "chip warn" }, "Next: v" + data.next_version_number);
    if (data.can_sign_off) {
      st.replaceChildren(next, " ", h("span", { class: "chip ok" }, "✓ Ready: no blockers"));
      box.replaceChildren(h("p", {}, "All active checks pass and there are no open issues."));
      return;
    }
    st.replaceChildren(next, " ", h("span", { class: "chip bad" }, data.blockers.length + " blocker" + (data.blockers.length === 1 ? "" : "s")));
    box.replaceChildren(h("div", { class: "row" }, h("span", { class: "chip warn" }, data.counts.checks + " check(s) not passing"),
      h("span", { class: "chip warn" }, data.counts.issues + " open issue(s)")), ...data.blockers.map(blockerRow));
  }

  function renderForm() {
    const card = document.getElementById("form-card"), btn = document.getElementById("so-btn"), hint = document.getElementById("so-hint");
    card.hidden = data.is_locked;
    const confirmed = document.getElementById("so-confirm").checked;
    let why = "";
    if (!isPartner()) why = "Only a Partner can sign off (you are " + App.state.user.roles.join(", ") + ").";
    else if (!data.can_sign_off) why = "Resolve every blocker above first.";
    else if (!confirmed) why = "Tick the confirmation to enable sign-off.";
    btn.disabled = !!why; hint.textContent = why;
    btn.textContent = "Sign Off & Lock Statements (v" + data.next_version_number + ")";
    renderReopen();
  }

  function renderReopen() {
    const card = document.getElementById("reopen-card"), btn = document.getElementById("ro-btn"), hint = document.getElementById("ro-hint");
    card.hidden = !data.is_locked;
    let why = "";
    if (!isPartner()) why = "Only a Partner can re-open a signed-off engagement (you are " + App.state.user.roles.join(", ") + ").";
    else if (!document.getElementById("ro-reason").value.trim()) why = "Enter the reason for re-opening.";
    btn.disabled = !!why; hint.textContent = why;
  }

  function renderVersions() {
    const tb = document.querySelector("#versions-table tbody");
    const udin = v => h("a", { href: "/udin" }, h("span", { class: "chip " + (v.udin_status === "recorded" ? "ok" : v.udin_status === "revoked" ? "bad" : "warn") },
      v.udin_status === "recorded" ? v.udin_number : v.udin_status === "revoked" ? "REVOKED" : "PENDING UDIN"));
    tb.replaceChildren(...(data.versions.length ? data.versions.map(v => h("tr", {},
      h("td", {}, h("b", {}, "v" + v.version_number)), h("td", {}, vBadge(v), " " + (v.status === "signed_locked" ? "Signed & locked" : "Superseded")),
      h("td", {}, v.signed_off_by_name), h("td", {}, new Date(v.signed_off_at).toLocaleString()),
      h("td", { class: "num" }, App.money(v.balance_sheet.total_assets)), h("td", { class: "num" }, App.money(v.profit_and_loss.profit_for_period)),
      h("td", {}, v.udin_id ? udin(v) : "—"),
      h("td", { class: "wrap" }, v.reopened_at ? v.reopened_by_name + ", " + new Date(v.reopened_at).toLocaleString() + " — “" + v.reopen_reason + "”" : "—")))
      : [h("tr", {}, h("td", { colspan: 8, class: "muted" }, "Nothing has been signed off yet."))]));
  }

  function fmtSnapshot(s) {
    const row = (k, v) => [h("dt", {}, k), h("dd", {}, v)];
    return h("dl", {},
      ...row("Total assets", App.money(s.balance_sheet.total_assets)), ...row("Total equity and liabilities", App.money(s.balance_sheet.total_equity_and_liabilities)),
      ...row("Revenue from operations", App.money(s.profit_and_loss.revenue_from_operations)), ...row("Total expenses", App.money(s.profit_and_loss.total_expenses)),
      ...row("Profit / (loss) for the period", App.money(s.profit_and_loss.profit_for_period)),
      ...row("Checks passing", s.checks.passed + " of " + s.checks.total), ...row("Approved adjustments", String(s.approved_adjustments)),
      ...row("Issues cleared", String(s.cleared_issues)), ...row("Data revision", String(s.data_revision)));
  }

  function renderCertificate() {
    const el = document.getElementById("certificate");
    if (!data.is_locked || !data.signoff) { el.replaceChildren(); return; }
    const r = data.signoff;
    el.replaceChildren(h("div", { class: "certificate" }, h("h2", {}, "🔒 Version v" + data.active_version.version_number + " signed off and locked"),
      h("div", {}, "Signed off by ", h("b", {}, r.signed_off_by_name), " on " + new Date(r.signed_off_at).toLocaleString() + " · FY " + r.snapshot_json.fy),
      r.signoff_note ? h("div", { class: "muted" }, "Note: " + r.signoff_note) : null, fmtSnapshot(r.snapshot_json),
      h("p", { class: "muted" }, "Amounts in " + App.unitLabel() + ". The statements, mappings, trial balance and adjustments are frozen.")));
  }

  function render() { renderGate(); renderForm(); renderCertificate(); renderVersions(); }

  async function load() { data = await App.api("/api/engagements/" + eid() + "/signoff"); render(); }

  async function signOff() {
    const btn = document.getElementById("so-btn");
    btn.disabled = true;
    try {
      await App.api("/api/engagements/" + eid() + "/signoff", { method: "POST", body: { note: document.getElementById("so-note").value || null } });
      await App.refreshHeader(); App.toast("Signed off. The engagement is now locked."); await load();
    } catch (err) {
      const blockers = err.detail && err.detail.blockers;
      App.toast(err.message, true);
      if (blockers) await load(); else renderForm();
    }
  }

  async function reopen() {
    const btn = document.getElementById("ro-btn");
    btn.disabled = true;
    try {
      await App.api("/api/engagements/" + eid() + "/signoff/reopen", { method: "POST", body: { reopen_reason: document.getElementById("ro-reason").value } });
      document.getElementById("ro-reason").value = ""; document.getElementById("so-confirm").checked = false;
      await App.refreshHeader(); App.toast("Engagement re-opened. Edits are allowed until the next version is signed off."); await load();
    } catch (err) { App.toast(err.message, true); renderReopen(); }
  }

  App.ready.then(async () => {
    document.getElementById("ro-btn").addEventListener("click", reopen);
    document.getElementById("ro-reason").addEventListener("input", () => data && renderReopen());
    document.getElementById("so-btn").addEventListener("click", signOff);
    document.getElementById("so-confirm").addEventListener("change", () => data && renderForm());
    App.onChange(what => { if (!data) return; if (what === "user") renderForm(); if (what === "unit") { renderCertificate(); renderVersions(); } });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
