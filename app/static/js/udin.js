/* UDIN dashboard (T-18): compliance cards, Partner-only record form with the 18-character ICAI format check, register with revoke. */
(function () {
  const { h } = App;
  const UDIN_RE = /^[A-Z0-9]{18}$/;
  let data = null;
  const eid = () => App.state.engagement.id;
  const isPartner = () => !!App.state.user && App.state.user.roles.includes("PARTNER");
  const card = (label, value, cls) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value " + (cls || "") }, String(value)));
  const chip = u => h("span", { class: "chip " + (u.status === "recorded" ? "ok" : u.status === "revoked" ? "bad" : "warn") },
    u.status === "recorded" ? "RECORDED" : u.status === "revoked" ? "REVOKED" : "PENDING UDIN");

  function renderCards() {
    const s = data.summary;
    document.getElementById("udin-cards").replaceChildren(card("Signed versions", s.total_versions), card("Pending UDIN", s.pending_count, s.pending_count ? "bad" : ""),
      card("Recorded UDIN", s.recorded_count), card("Revoked", s.revoked_count));
  }

  function renderForm() {
    const sel = document.getElementById("u-version"), keep = sel.value;
    const pending = data.items.filter(u => u.status === "pending_udin");
    sel.replaceChildren(...(pending.length ? pending.map(u => h("option", { value: u.version_id }, "v" + u.version_number + (u.version_status === "superseded" ? " (superseded)" : ""))) : [h("option", { value: "" }, "No version awaiting a UDIN")]));
    if (keep && pending.some(u => String(u.version_id) === keep)) sel.value = keep;
    updateHint();
  }

  function updateHint() {
    const n = document.getElementById("u-number").value.trim(), d = document.getElementById("u-date").value, v = document.getElementById("u-version").value;
    let why = "";
    if (!isPartner()) why = "Only a Partner can record a UDIN (you are " + App.state.user.roles.join(", ") + ").";
    else if (!v) why = "Every signed version already has a UDIN.";
    else if (!UDIN_RE.test(n)) why = "A UDIN is exactly 18 characters: uppercase letters and digits (" + n.length + " entered).";
    else if (!d) why = "Choose the date the UDIN was generated.";
    document.getElementById("u-save").disabled = !!why; document.getElementById("u-hint").textContent = why;
  }

  function renderTable() {
    const rows = data.items.map(u => {
      const act = [];
      if (u.status === "recorded" && isPartner()) act.push(h("button", { type: "button", onclick: () => revoke(u) }, "Revoke"));
      return h("tr", {}, h("td", {}, h("b", {}, "v" + u.version_number)),
        h("td", {}, h("span", { class: "chip " + (u.version_status === "signed_locked" ? "ok" : "warn") }, u.version_status === "signed_locked" ? "Signed & locked" : "Superseded")),
        h("td", {}, u.signed_off_by_name + ", " + new Date(u.signed_off_at).toLocaleDateString()),
        h("td", {}, u.udin_number ? h("code", {}, u.udin_number) : "—"), h("td", {}, chip(u), u.revoked_reason ? h("div", { class: "muted wrap" }, "“" + u.revoked_reason + "”") : null),
        h("td", {}, u.udin_generated_date || "—"), h("td", {}, u.recorded_by_name ? u.recorded_by_name + ", " + new Date(u.recorded_at).toLocaleString() : "—"),
        h("td", {}, ...act));
    });
    document.querySelector("#udin-table tbody").replaceChildren(...(rows.length ? rows : [h("tr", {}, h("td", { colspan: 8, class: "muted" }, "Nothing has been signed off yet, so no UDIN is due."))]));
  }

  const render = () => { renderCards(); renderForm(); renderTable(); };
  async function load() { data = await App.api("/api/engagements/" + eid() + "/udin"); render(); }

  async function save() {
    const btn = document.getElementById("u-save"); btn.disabled = true;
    try {
      await App.api("/api/engagements/" + eid() + "/udin/" + document.getElementById("u-version").value, { method: "POST", body: {
        udin_number: document.getElementById("u-number").value.trim(), udin_generated_date: document.getElementById("u-date").value,
        document_type: document.getElementById("u-doc").value } });
      document.getElementById("u-number").value = ""; App.toast("UDIN recorded."); await load();
    } catch (err) { App.toast(err.message, true); updateHint(); }
  }

  async function revoke(u) {
    const reason = window.prompt("Reason for revoking UDIN " + u.udin_number + " (required):");
    if (reason === null) return;
    try {
      await App.api("/api/engagements/" + eid() + "/udin/" + u.version_id + "/revoke", { method: "POST", body: { revoked_reason: reason } });
      App.toast("UDIN revoked."); await load();
    } catch (err) { App.toast(err.message, true); }
  }

  App.ready.then(async () => {
    const num = document.getElementById("u-number");
    num.addEventListener("input", () => { num.value = num.value.toUpperCase(); updateHint(); });
    ["u-date", "u-version"].forEach(id => document.getElementById(id).addEventListener("input", updateHint));
    document.getElementById("u-save").addEventListener("click", save);
    App.onChange(what => { if (data && what === "user") { renderForm(); renderTable(); } });
    await load();
  }).catch(err => App.toast(err.message, true));
})();
