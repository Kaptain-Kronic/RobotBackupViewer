/* theme_ui.js - the theme PICKER: one row inside the ⚙ settings dialog whose
   button drops an anchored panel holding every theme.

   It replaces the old two-tab 🎨 window. That window put a 28-row accordion on
   screen permanently — tall enough to scroll the dialog at the DEFAULT text size
   — and split "how the app looks" across a second topbar button. Now the whole
   theme catalog is one line until you go looking for it, and a filter makes 29
   themes findable (the accordion had no search).

   settings_ui.js owns the dialog and the other rows; theme.js owns the theme
   data + the color editor this row hops into.

   Contract: hover previews live, leaving the list drops the preview, a click
   commits. Nothing in the picker hides behind a fold. */
(function () {
  "use strict";

  /* Custom leads: your own themes are the ones you switch between, the bundled
     packs are a catalog you visit once. Anything unlisted slots in before the
     last entry. */
  var CAT_ORDER = ["Custom", "MonkeyType", "Sports", "Cyberpunk 2077", "Vibes"];
  /* attribution shown under each pack */
  var CREDITS = {
    "Custom": "your own — saved as shareable files",
    "MonkeyType": "themes adapted from monkeytype.com",
    "Sports": "team & university colors © their respective owners",
    "Cyberpunk 2077": "inspired by Cyberpunk 2077 — © CD Projekt Red",
    "Vibes": "inspired by retro-futuristic gradient art",
  };

  /* a theme's pack; legacy themes with no category fall under MonkeyType */
  function catOf(t) { return t.category || "MonkeyType"; }

  function swatchHtml(t) {
    return ["bg", "accent", "text", "sub"].map(function (k) {
      return '<span class="swatch" style="background:' + BV.esc((t.colors || {})[k] || "#000") + '"></span>';
    }).join("");
  }

  /* The panel's contents. ctx: {committedId, commit(id), close(), edit(theme)}
     — commit() also moves ctx.committedId, because the hover-off restore reads
     it and a stale snapshot would drop you back onto the theme you had BEFORE
     the pick (the pick would look like it un-did itself). */
  function buildList(ctx) {
    var wrap = BV.el("div", { class: "theme-pick-list" });
    var host = BV.el("div", { class: "theme-acc" });
    var sb = BV.searchBox({
      placeholder: "filter themes…",
      onChange: function (q) { paint(q); },
    });
    var searchWrap = BV.el("div", { class: "theme-pick-search" });
    searchWrap.appendChild(sb.el);
    wrap.appendChild(searchWrap);
    wrap.appendChild(host);

    var focusEl = null;
    function visRows() {
      return Array.prototype.slice.call(host.querySelectorAll(".opt-row[data-theme-id]"));
    }
    function setFocus(el, preview) {
      if (focusEl) focusEl.classList.remove("focused");
      focusEl = el || null;
      if (!focusEl) return;
      focusEl.classList.add("focused");
      focusEl.scrollIntoView({ block: "nearest" });
      if (preview && focusEl.dataset.themeId) BV.theme.applyById(focusEl.dataset.themeId, false);
    }
    function moveFocus(d) {
      var rows = visRows();
      if (!rows.length) return;
      var i = rows.indexOf(focusEl);
      if (i < 0) { setFocus(rows[0], true); return; }
      setFocus(rows[(i + d + rows.length) % rows.length], true);
    }

    /* leaving the list ends the preview: a hover that previews but never
       un-previews leaves you looking at a theme you did not choose, with no way
       back except finding the committed row again by eye */
    wrap.addEventListener("mouseleave", function () {
      if (focusEl) focusEl.classList.remove("focused");
      focusEl = null;
      BV.theme.applyById(ctx.committedId, false);
    });

    function makeRow(t) {
      var r = BV.el("div", { class: "opt-row" + (t.id === ctx.committedId ? " sel" : "") },
        '<span class="name">' + BV.esc(t.name || t.id) + "</span>" +
        '<span class="swatches">' + swatchHtml(t) + "</span>");
      r.dataset.themeId = t.id;
      r.addEventListener("mouseenter", function () {
        setFocus(r, false);
        BV.theme.applyById(t.id, false);
      });
      r.addEventListener("click", function () {
        ctx.commit(t.id);
        var prev = host.querySelector(".opt-row.sel");
        if (prev) prev.classList.remove("sel");
        r.classList.add("sel");
        BV.toast(t.name || t.id);
        if (ctx.close) ctx.close();
      });
      if (t.user) {
        var edit = BV.el("button", { class: "opt-edit", title: "edit (e)" }, "✎");
        edit.addEventListener("click", function (e) {
          e.stopPropagation();
          ctx.edit(t);
        });
        r.appendChild(edit);
        var del = BV.el("button", { class: "opt-del", title: "delete" }, "🗑");
        del.addEventListener("click", function (e) {
          e.stopPropagation();
          BV.api.call("delete_user_theme", t.id).then(function () {
            BV.theme.themes = BV.theme.themes.filter(function (x) { return x.id !== t.id; });
            if (focusEl === r) focusEl = null;
            r.remove();
            if (BV.theme.activeId === t.id || ctx.committedId === t.id) {
              ctx.commit("serika_dark");
            }
            BV.toast("deleted " + (t.name || t.id));
          }).catch(function () { BV.toast("delete failed"); });
        });
        r.appendChild(del);
      }
      return r;
    }

    function paint(q) {
      q = (q || "").toLowerCase();
      host.innerHTML = "";
      focusEl = null;
      var all = BV.theme.themes;
      var hits = q ? all.filter(function (t) {
        return (t.name || t.id).toLowerCase().indexOf(q) >= 0 ||
          catOf(t).toLowerCase().indexOf(q) >= 0;
      }) : all;
      sb.setCount(q ? hits.length : undefined, all.length);

      var present = [];
      hits.forEach(function (t) { var c = catOf(t); if (present.indexOf(c) < 0) present.push(c); });
      /* Custom always has a section (even empty) so "no custom themes yet" can
         point at the ＋ — unless a filter is narrowing the list */
      if (!q && present.indexOf("Custom") < 0) present.push("Custom");
      present.sort(function (a, b) {
        var ia = CAT_ORDER.indexOf(a), ib = CAT_ORDER.indexOf(b);
        if (ia < 0) ia = CAT_ORDER.length - 1.5;
        if (ib < 0) ib = CAT_ORDER.length - 1.5;
        return ia - ib;
      });

      present.forEach(function (cat) {
        var list = hits.filter(function (t) { return catOf(t) === cat; });
        var head = BV.el("div", { class: "acc-head" },
          '<span class="acc-name">' + BV.esc(cat) + "</span>" +
          '<span class="acc-count">' + list.length + "</span>");
        var bodyEl = BV.el("div", { class: "acc-body" });
        if (cat === "Custom") {
          var reveal = BV.el("button", { class: "acc-reveal", title: "open themes folder" }, "📁");
          reveal.addEventListener("click", function () {
            BV.api.call("reveal_themes_dir").catch(function () {});
          });
          head.appendChild(reveal);
        }
        list.forEach(function (t) { bodyEl.appendChild(makeRow(t)); });
        if (cat === "Custom" && !list.length) {
          bodyEl.appendChild(BV.el("div", { class: "acc-empty" }, "no custom themes yet — ＋ makes one"));
        }
        if (CREDITS[cat]) bodyEl.appendChild(BV.el("div", { class: "acc-credit" }, BV.esc(CREDITS[cat])));
        host.appendChild(head);
        host.appendChild(bodyEl);
      });

      /* land on the committed theme so j/k starts where you are */
      setFocus(host.querySelector('.opt-row[data-theme-id="' + ctx.committedId + '"]') || visRows()[0], false);
    }
    paint("");

    return {
      el: wrap,
      focus: function () { sb.focus(); },
      onKey: function (e) {
        if (e.key === "ArrowDown" || e.key === "j") { moveFocus(1); return true; }
        if (e.key === "ArrowUp" || e.key === "k") { moveFocus(-1); return true; }
        if (e.key === "Enter" && focusEl && focusEl.dataset.themeId) {
          ctx.commit(focusEl.dataset.themeId);
          if (ctx.close) ctx.close();
          return true;
        }
        if (e.key === "e" && focusEl && focusEl.dataset.themeId) {
          var t = BV.theme.themes.find(function (x) { return x.id === focusEl.dataset.themeId; });
          if (t && t.user) { ctx.edit(t); return true; }
        }
        return false;
      },
    };
  }

  BV.themeUI = {
    /* The row: ＋ then the current theme, tight together at the right edge so
       the picker lines up with the segs and sliders above it.

       opts.closeHost() runs before hopping to the color editor — BV.modal does
       not stack, so the settings dialog has to step aside. Returns
       {closePanel}: the dialog calls it on close, because the panel lives on
       <html> and would otherwise outlive the dialog it belongs to. */
    row: function (into, opts) {
      opts = opts || {};
      var committedId = BV.theme.activeId;
      var open = null;

      var r = BV.el("div", { class: "set-row" });
      r.appendChild(BV.el("span", { class: "name" }, "theme"));
      var group = BV.el("div", { class: "set-theme" });
      var addBtn = BV.el("button", { class: "btn theme-new", title: "new custom theme" }, "＋");
      var btn = BV.el("button", { class: "btn theme-pick" });
      group.appendChild(addBtn);
      group.appendChild(btn);
      r.appendChild(group);
      into.appendChild(r);

      function syncBtn() {
        var t = BV.theme.themes.find(function (x) { return x.id === committedId; }) ||
          { name: "—", colors: {} };
        btn.innerHTML = '<span class="nm">' + BV.esc(t.name || t.id) + "</span>" +
          '<span class="swatches">' + swatchHtml(t) + "</span>" +
          '<span class="caret">▾</span>';
        btn.title = "theme — " + (t.name || t.id);
      }
      syncBtn();

      /* the editor is its own dialog: close the panel, close the host, hop */
      function hopToEditor(theme) {
        if (open) open.close();
        if (opts.closeHost) opts.closeHost();
        BV.theme.editTheme(theme, committedId);
      }
      addBtn.addEventListener("click", function () { hopToEditor(null); });

      btn.addEventListener("click", function () {
        if (open) { open.close(); return; }
        var ctx = {
          committedId: committedId,
          commit: function (id) {
            committedId = id;
            ctx.committedId = id;
            BV.theme.applyById(id, true);
            syncBtn();
          },
          close: function () { if (open) open.close(); },
          edit: hopToEditor,
        };
        var list = buildList(ctx);
        var p = BV.dropPanel(btn, list.el, {
          align: "right",
          onKey: list.onKey,
          onClose: function () {
            open = null;
            /* leaving without a pick settles back onto the committed theme */
            BV.theme.applyById(committedId, false);
          },
        });
        if (!p) return;                        /* swallowed half of a toggle */
        open = p;
        list.focus();
      });

      return { closePanel: function () { if (open) open.close(); } };
    },
  };
})();
