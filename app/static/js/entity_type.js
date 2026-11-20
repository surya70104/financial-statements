/* Entity type selector (Q2): a partnership shows Partners' Capital / Current Account instead of Share capital / Reserves and surplus. */
(function () {
  const sel = document.getElementById("entity-type"), hint = document.getElementById("entity-hint");
  const canSet = () => !!App.state.user && App.state.user.roles.some(r => r === "CHECKER" || r === "PARTNER");

  function render() {
    const e = App.state.engagement;
    sel.value = e.entity_type;
    const locked = !!e.is_locked, allowed = canSet();
    sel.disabled = locked || !allowed;
    hint.textContent = locked ? "Locked after Partner sign-off." : !allowed ? "Only a Checker or Partner can change the entity type." :
      "Template " + e.template_name + " (confirmed). A partnership relabels the equity lines.";
  }

  App.ready.then(() => {
    render();
    App.onChange(what => { if (what === "user") render(); });
    sel.addEventListener("change", async () => {
      try {
        const r = await App.api("/api/engagements/" + App.state.engagement.id + "/entity-type", { method: "PATCH", body: { entity_type: sel.value } });
        await App.refreshHeader(); render(); App.toast(r.changed ? "Entity type set to " + r.label + ". The statements now use these labels." : "No change.");
      } catch (err) { App.toast(err.message, true); render(); }
    });
  }).catch(err => App.toast(err.message, true));
})();
