/* Mapping screen: one Schedule III line + one note per ledger, saved immediately. */
(function () {
  const { h } = App;
  let data = null;
  const eid = () => App.state.engagement.id;
  const lineById = id => data.lines.find(l => l.id === id);

  async function load() {
    data = await App.api("/api/engagements/" + eid() + "/mappings");
    const groups = [...new Set(data.ledgers.map(l => l.tally_group))].sort();
    const sel = document.getElementById("map-group");
    const keep = sel.value;
    sel.replaceChildren(h("option", { value: "" }, "All Tally groups"), ...groups.map(g => h("option", { value: g }, g)));
    sel.value = groups.includes(keep) ? keep : "";
    renderCounters(data.counts);
    renderRows();
  }

  function renderCounters(c) {
    App.setUnmappedBadge(c.unmapped_ledgers);
    document.getElementById("counters").replaceChildren(
      h("span", { class: "counter " + (c.unmapped_ledgers ? "bad" : "ok") }, "Unmapped ledgers: " + c.unmapped_ledgers + " of " + c.total_ledgers),
      h("span", { class: "counter " + (c.open_mapping_exceptions ? "bad" : "ok") }, "Open mapping exceptions: " + c.open_mapping_exceptions),
      h("span", { class: "counter " + (c.matches ? "ok" : "bad") }, c.matches ? "✓ counts match" : "⚠ counts differ"));
  }

  function lineSelect(row) {
    const sel = h("select", { "aria-label": "Schedule III line for " + row.name, disabled: !App.canEdit() },
      h("option", { value: "" }, "— not mapped —"));
    const sections = new Map();
    data.lines.forEach(l => {
      const key = (l.statement === "BS" ? "Balance Sheet" : "P&L") + " – " + l.section;
      if (!sections.has(key)) sections.set(key, h("optgroup", { label: key }));
      sections.get(key).append(h("option", { value: l.id }, l.label));
    });
    sections.forEach(g => sel.append(g));
    sel.value = row.line_id || "";
    return sel;
  }

  function noteSelect(row, lineId, noteId) {
    const line = lineById(Number(lineId));
    const sel = h("select", { "aria-label": "Note for " + row.name, disabled: !App.canEdit() || !line },
      h("option", { value: "" }, line ? "choose note…" : "—"));
    if (line) line.notes.forEach(n => sel.append(h("option", { value: n.id }, "Note " + n.note_no + " – " + n.title)));
    sel.value = noteId || "";
    return sel;
  }

  function statusChip(row) {
    return row.is_mapped ? h("span", { class: "chip ok" }, "mapped") : h("span", { class: "chip bad" }, "unmapped");
  }

  async function save(row, tr, lineSel, noteSel) {
    const lineId = lineSel.value ? Number(lineSel.value) : null;
    try {
      let res;
      if (!lineId) {
        res = await App.api("/api/engagements/" + eid() + "/mappings/" + row.ledger_id, { method: "DELETE" });
        Object.assign(row, { line_id: null, note_id: null, is_mapped: false });
      } else {
        res = await App.api("/api/engagements/" + eid() + "/mappings/" + row.ledger_id,
          { method: "POST", body: { line_id: lineId, note_id: noteSel.value ? Number(noteSel.value) : null } });
        Object.assign(row, { line_id: res.line_id, note_id: res.note_id, is_mapped: true });
      }
      data.counts = Object.assign(data.counts, res.counts);
      renderCounters(data.counts);
      tr.replaceWith(buildRow(row, Number(tr.dataset.idx)));
      if (document.getElementById("map-status").value !== "all") renderRows();
      if (res.changed) App.toast(row.name + (lineId ? " mapped." : " unmapped."));
    } catch (err) {
      App.toast(err.message, true);
      await load();                                  // resync the screen with what the server really has
    }
  }

  function buildRow(row, idx) {
    const lineSel = lineSelect(row);
    let noteSel = noteSelect(row, row.line_id, row.note_id);
    const tr = h("tr", { "data-idx": idx },
      h("td", {}, idx), h("td", {}, row.name), h("td", {}, row.tally_group || ""),
      h("td", { class: "num" }, App.money(row.closing_balance)),
      h("td", {}, lineSel), h("td", { class: "note-cell" }, noteSel), h("td", {}, statusChip(row)), h("td", {}));
    lineSel.addEventListener("change", () => {
      const line = lineById(Number(lineSel.value));
      const fresh = noteSelect(row, lineSel.value, line && line.notes.length === 1 ? line.notes[0].id : "");
      noteSel.replaceWith(fresh); noteSel = fresh;
      fresh.addEventListener("change", () => save(row, tr, lineSel, noteSel));
      if (!line || line.notes.length === 1) save(row, tr, lineSel, noteSel);
    });
    noteSel.addEventListener("change", () => save(row, tr, lineSel, noteSel));
    return tr;
  }

  function renderRows() {
    const q = document.getElementById("map-search").value.trim().toLowerCase();
    const st = document.getElementById("map-status").value;
    const grp = document.getElementById("map-group").value;
    document.getElementById("map-table").querySelectorAll("th.num").forEach(th => { th.textContent = "Closing (" + App.unitLabel() + ")"; });
    const rows = data.ledgers.map((r, i) => [r, i + 1]).filter(([r]) =>
      (!q || r.name.toLowerCase().includes(q)) && (st === "all" || (st === "mapped") === r.is_mapped) && (!grp || r.tally_group === grp));
    document.querySelector("#map-table tbody").replaceChildren(...rows.map(([r, i]) => buildRow(r, i)));
  }

  async function autoMap() {
    const btn = document.getElementById("auto-map");
    btn.disabled = true;
    try {
      const res = await App.api("/api/engagements/" + eid() + "/mappings/auto-map", { method: "POST" });
      App.toast(res.mapped + " ledger(s) mapped" + (res.skipped.length ? "; " + res.skipped.length + " need manual mapping." : "."), res.skipped.length > 0);
      await load();
    } catch (err) { App.toast(err.message, true); }
    finally { btn.disabled = !App.canEdit(); }
  }

  App.ready.then(() => {
    const am = document.getElementById("auto-map");
    am.disabled = !App.canEdit();
    am.addEventListener("click", autoMap);
    App.onChange(what => { if (what === "user") am.disabled = !App.canEdit(); });
    ["map-search", "map-status", "map-group"].forEach(id => document.getElementById(id).addEventListener("input", renderRows));
    App.onChange(what => { if (data) renderRows(); });
    return load();
  }).catch(err => App.toast(err.message, true));
})();
