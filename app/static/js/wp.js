/* Working Papers register: coverage across every statement line and note (T-14). */
(function () {
  const { h } = App;

  function status(t) {
    if (!t.link_count) return ["bad", "NO WP"];
    return t.reviewed_count === t.link_count ? ["ok", "REVIEWED"] : ["warn", "ATTACHED"];
  }

  function render() {
    const d = WP.data;
    if (!d) return;
    const s = d.summary;
    document.getElementById("wp-summary").replaceChildren(
      h("span", { class: "chip " + (s.statement_lines.covered === s.statement_lines.total ? "ok" : "warn") },
        "Lines: " + s.statement_lines.covered + " of " + s.statement_lines.total + " covered"),
      h("span", { class: "chip " + (s.notes.covered === s.notes.total ? "ok" : "warn") }, "Notes: " + s.notes.covered + " of " + s.notes.total + " covered"),
      h("span", { class: "muted" }, s.reviewed_links + " of " + s.total_links + " papers reviewed"));
    const type = document.getElementById("wp-type").value, cover = document.getElementById("wp-cover").value;
    const rows = d.targets.filter(t => (!type || t.target_type === type) && (!cover ||
      (cover === "none" && !t.link_count) || (cover === "reviewed" && t.link_count && t.reviewed_count === t.link_count) ||
      (cover === "unreviewed" && t.link_count && t.reviewed_count < t.link_count)));
    const tbody = document.querySelector("#wp-table tbody");
    if (!rows.length) { tbody.replaceChildren(h("tr", {}, h("td", { colspan: 7, class: "muted" }, "Nothing matches these filters."))); return; }
    tbody.replaceChildren(...rows.map(t => {
      const [cls, label] = status(t);
      return h("tr", {}, h("td", {}, t.target_type === "note" ? "Note" : "Line"), h("td", {}, t.statement === "BS" ? "Balance Sheet" : "Profit & Loss"),
        h("td", { class: "wrap" }, t.label, h("div", { class: "sub" }, t.target_code)),
        h("td", { class: "num" }, t.link_count), h("td", { class: "num" }, t.reviewed_count),
        h("td", {}, h("span", { class: "chip " + cls }, label)), h("td", {}, WP.chip(t.target_type, t.target_code, t.label)));
    }));
  }

  App.ready.then(async () => {
    ["wp-type", "wp-cover"].forEach(id => document.getElementById(id).addEventListener("change", render));
    WP.subscribe(render);
    await WP.load();
  }).catch(err => App.toast(err.message, true));
})();
