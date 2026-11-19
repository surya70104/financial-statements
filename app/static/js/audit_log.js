/* Audit trail viewer: read-only, newest first, paged with "Load older entries". */
(function () {
  const { h } = App;
  const PAGE = 100;
  const TABLES = ["engagements", "ledgers", "ledger_balances", "tally_groups", "upload_history", "ledger_mappings", "exceptions", "journal_entries",
                  "journal_lines", "working_paper_links", "engagement_signoffs", "prior_year_amounts", "statement_versions", "check_runs"];
  let rows = [], exhausted = false;
  const eid = () => App.state.engagement.id;

  function summary(a) {
    if (a.action === "EVENT") {
      const d = a.after_data || {};
      const bits = Object.entries(d).filter(([, v]) => v === null || typeof v !== "object").slice(0, 4).map(([k, v]) => k.replace(/_/g, " ") + ": " + v);
      return (a.reason ? "“" + a.reason + "” " : "") + bits.join(" · ");
    }
    return a.changed_fields && a.changed_fields.length ? "Fields: " + a.changed_fields.join(", ") : (a.action === "INSERT" ? "Row created" : "Row deleted");
  }

  function dataBlock(a) {
    const parts = [];
    if (a.before_data) parts.push("Before:\n" + JSON.stringify(a.before_data, null, 2));
    if (a.after_data) parts.push("After:\n" + JSON.stringify(a.after_data, null, 2));
    return parts.length ? h("details", {}, h("summary", { class: "muted" }, "Show data"), h("pre", { class: "audit-json" }, parts.join("\n\n"))) : null;
  }

  function render() {
    const q = document.getElementById("a-search").value.trim().toLowerCase();
    const shown = rows.filter(a => !q || (a.user_name + " " + (a.event_name || "") + " " + a.entity_table + " " + a.action + " " + summary(a)).toLowerCase().includes(q));
    document.getElementById("audit-count").textContent = shown.length + " of " + rows.length + " loaded entries shown";
    document.querySelector("#audit-table tbody").replaceChildren(...(shown.length ? shown.map(a => h("tr", {},
      h("td", {}, a.id), h("td", {}, new Date(a.occurred_at).toLocaleString()), h("td", {}, a.user_name),
      h("td", {}, h("span", { class: "chip " + (a.action === "EVENT" ? "ok" : a.action === "DELETE" ? "bad" : "warn") }, a.action === "EVENT" ? a.event_name : a.action)),
      h("td", {}, a.entity_table + (a.entity_id ? " #" + a.entity_id : "")), h("td", { class: "wrap" }, summary(a), dataBlock(a))))
      : [h("tr", {}, h("td", { colspan: 6, class: "muted" }, "No entries match."))]));
    document.getElementById("a-more").hidden = exhausted;
  }

  async function load(more) {
    const params = new URLSearchParams({ limit: PAGE, scope: document.getElementById("a-scope").value });
    const t = document.getElementById("a-table").value;
    if (t) params.set("entity_table", t);
    if (more && rows.length) params.set("before_id", rows[rows.length - 1].id);
    const got = await App.api("/api/engagements/" + eid() + "/audit-logs?" + params);
    rows = more ? rows.concat(got) : got;
    exhausted = got.length < PAGE;
    render();
  }

  App.ready.then(async () => {
    const sel = document.getElementById("a-table");
    TABLES.forEach(t => sel.append(h("option", { value: t }, t.replace(/_/g, " "))));
    ["a-scope", "a-table"].forEach(id => document.getElementById(id).addEventListener("change", () => load(false).catch(e => App.toast(e.message, true))));
    document.getElementById("a-search").addEventListener("input", render);
    document.getElementById("a-more").addEventListener("click", () => load(true).catch(e => App.toast(e.message, true)));
    await load(false);
  }).catch(err => App.toast(err.message, true));
})();
