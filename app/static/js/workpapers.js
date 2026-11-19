/* Shared working-paper (T-14) helper: data cache, the 📎 chip, and the attach/review/remove drawer.
 * Pages call WP.load() after App.ready, WP.subscribe(fn) to re-render when links change, WP.chip(type, code, title) for the chip. */
const WP = (() => {
  const { h } = App;
  let data = null, panelFor = null, overlay = null;
  const subs = [];
  const eid = () => App.state.engagement.id;
  const base = () => "/api/engagements/" + eid() + "/working-papers";
  const key = (t, c) => t + ":" + c;
  const links = (t, c) => (data ? data.by_target[key(t, c)] || [] : []);
  const roles = () => (App.state.user ? App.state.user.roles : []);
  const safeUrl = u => /^https?:\/\//i.test(u || "");

  async function load() {
    data = await App.api(base());
    subs.forEach(fn => fn());
    if (panelFor) renderPanel();
    return data;
  }

  function chip(type, code, title) {
    const ls = links(type, code), all = ls.length && ls.every(l => l.status === "reviewed");
    const label = ls.length ? "📎 " + ls.length + " WP" + (all ? " ✓" : "") : "+ Attach WP";
    return h("button", { type: "button", class: "wp-chip " + (ls.length ? (all ? "reviewed" : "has") : "none"),
      title: ls.length ? ls.length + " working paper(s)" + (all ? ", all reviewed" : "") : "No working paper yet: click to attach",
      onclick: e => { e.preventDefault(); e.stopPropagation(); open(type, code, title); } }, label);
  }

  function close() { panelFor = null; if (overlay) { overlay.remove(); overlay = null; } }

  function open(type, code, title) {
    panelFor = { type, code, title };
    if (!overlay) {
      overlay = h("div", { class: "wp-overlay", role: "dialog", "aria-modal": "true", "aria-label": "Working papers" });
      overlay.addEventListener("click", e => { if (e.target === overlay) close(); });
      document.body.append(overlay);
    }
    renderPanel();
  }

  document.addEventListener("keydown", e => { if (e.key === "Escape" && panelFor) close(); });

  async function run(promise, okMessage) {
    try { await promise; App.toast(okMessage); await load(); } catch (err) { App.toast(err.message, true); }
  }

  function linkRow(l) {
    const me = App.state.user, locked = App.isLocked();
    const canReview = !locked && roles().some(r => r === "CHECKER" || r === "PARTNER") && me && l.attached_by !== me.id && l.status !== "reviewed";
    const reviewWhy = locked ? "Locked after sign-off" : l.status === "reviewed" ? "Already reviewed"
      : !roles().some(r => r === "CHECKER" || r === "PARTNER") ? "Only a Checker or Partner can review"
      : me && l.attached_by === me.id ? "You attached this: someone else must review it" : "Mark as reviewed";
    const canRemove = !locked && l.status !== "reviewed";
    return h("div", { class: "wp-row" },
      h("div", { class: "wp-main" },
        h("div", {}, h("b", {}, l.wp_ref_code), " – ", l.wp_title, " ",
          h("span", { class: "chip " + (l.status === "reviewed" ? "ok" : "warn") }, l.status === "reviewed" ? "REVIEWED" : "ATTACHED")),
        l.wp_url_or_path ? h("div", { class: "sub" }, safeUrl(l.wp_url_or_path)
          ? h("a", { href: l.wp_url_or_path, target: "_blank", rel: "noopener noreferrer" }, l.wp_url_or_path) : l.wp_url_or_path) : null,
        l.notes ? h("div", { class: "sub" }, "Note: " + l.notes) : null,
        h("div", { class: "sub" }, "Attached by " + l.attached_by_name + ", " + new Date(l.attached_at).toLocaleString() +
          (l.reviewed_by_name ? " · Reviewed by " + l.reviewed_by_name + ", " + new Date(l.reviewed_at).toLocaleString() : ""))),
      h("div", { class: "wp-actions" },
        h("button", { type: "button", class: canReview ? "primary" : "", disabled: !canReview, title: reviewWhy,
          onclick: () => run(App.api(base() + "/" + l.id + "/review", { method: "POST" }), l.wp_ref_code + " marked reviewed.") }, "Mark Reviewed"),
        h("button", { type: "button", disabled: !canRemove, title: canRemove ? "Remove this link" : locked ? "Locked after sign-off" : "Reviewed links cannot be removed",
          onclick: () => run(App.api(base() + "/" + l.id, { method: "DELETE" }), l.wp_ref_code + " removed.") }, "Remove")));
  }

  function renderPanel() {
    const { type, code, title } = panelFor, ls = links(type, code), locked = App.isLocked();
    const f = {};
    const field = (name, label, ph, wide) => { f[name] = h("input", { type: "text", placeholder: ph, "aria-label": label, maxlength: name === "url" ? 1000 : 200, disabled: locked, class: wide ? "wide" : "" }); return h("label", {}, label, f[name]); };
    const form = h("div", { class: "wp-form" },
      h("h3", {}, "Attach a working paper"),
      h("div", { class: "row" }, field("ref", "WP code", "WP-B1"), field("title", "Title", "Bank reconciliation", true)),
      h("div", { class: "row" }, field("url", "Document link / path", "https://… or \\\\server\\share\\file.xlsx", true), field("notes", "Note", "optional", true)),
      h("button", { type: "button", class: "primary", disabled: locked, title: locked ? "Locked after sign-off" : "Attach", onclick: () =>
        run(App.api(base(), { method: "POST", body: { target_type: type, target_code: code, wp_ref_code: f.ref.value, wp_title: f.title.value,
          wp_url_or_path: f.url.value || null, notes: f.notes.value || null } }), "Working paper attached.") }, "Attach"));
    overlay.replaceChildren(h("div", { class: "wp-panel" },
      h("div", { class: "row between" }, h("h2", {}, "Working papers – " + title), h("button", { type: "button", onclick: close, "aria-label": "Close" }, "✕")),
      locked ? h("div", { class: "panel bad" }, "🔒 The engagement is locked after Partner sign-off: working papers can no longer be changed.") : null,
      ls.length ? h("div", {}, ...ls.map(linkRow)) : h("p", { class: "muted" }, "No working paper is attached to this " + (type === "note" ? "note" : "line") + " yet."),
      form));
  }

  App.onChange(what => { if (panelFor && (what === "user")) renderPanel(); });

  return { load, chip, open, close, subscribe: fn => subs.push(fn), links, get data() { return data; } };
})();
