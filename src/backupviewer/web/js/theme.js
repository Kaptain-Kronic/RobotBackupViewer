/* theme.js - MonkeyType-style theming: a theme is ~9 colors applied as CSS vars. */
(function () {
  "use strict";

  var VAR_MAP = {
    bg: "--bg", bg2: "--bg2", sub: "--sub", subAlt: "--sub-alt",
    text: "--text", accent: "--accent", error: "--error", ok: "--ok", warn: "--warn",
  };

  BV.theme = {
    themes: [],
    activeId: null,

    apply: function (theme) {
      var root = document.documentElement;
      Object.keys(VAR_MAP).forEach(function (k) {
        if (theme.colors[k]) root.style.setProperty(VAR_MAP[k], theme.colors[k]);
      });
      BV.theme.activeId = theme.id;
      BV.state.emit("theme", theme);
    },

    applyById: function (id, persist) {
      var t = BV.theme.themes.find(function (x) { return x.id === id; });
      if (!t) return;
      BV.theme.apply(t);
      if (persist) BV.api.call("set_setting", "theme", id).catch(function () {});
    },

    load: function () {
      return BV.api.call("get_themes").then(function (data) {
        BV.theme.themes = data.themes;
        var active = data.themes.find(function (t) { return t.id === data.active; });
        if (active) BV.theme.apply(active);
        return data;
      });
    },

    cycle: function () {
      if (!BV.theme.themes.length) return;
      var i = BV.theme.themes.findIndex(function (t) { return t.id === BV.theme.activeId; });
      var next = BV.theme.themes[(i + 1) % BV.theme.themes.length];
      BV.theme.applyById(next.id, true);
      BV.toast(next.name);
    },

    picker: function () {
      if (!BV.theme.themes.length) { BV.toast("no themes loaded"); return; }
      var body = BV.el("div");
      var startId = BV.theme.activeId;
      var chosen = false;
      var focused = Math.max(0, BV.theme.themes.findIndex(function (t) { return t.id === startId; }));
      var rows = BV.theme.themes.map(function (t, i) {
        var sw = ["bg", "accent", "text", "sub"].map(function (k) {
          return '<span class="swatch" style="background:' + BV.esc(t.colors[k] || "#000") + '"></span>';
        }).join("");
        var row = BV.el("div", { class: "opt-row" + (t.id === startId ? " sel" : "") },
          '<span class="name">' + BV.esc(t.name || t.id) + (t.user ? ' <span class="dim">(user)</span>' : "") + "</span>" +
          '<span class="swatches">' + sw + "</span>");
        row.addEventListener("mouseenter", function () { setFocus(i, false); BV.theme.applyById(t.id, false); });
        row.addEventListener("click", function () {
          chosen = true;
          BV.theme.applyById(t.id, true);
          modal.close();
        });
        return row;
      });
      rows.forEach(function (r) { body.appendChild(r); });

      function setFocus(i, preview) {
        focused = (i + rows.length) % rows.length;
        rows.forEach(function (r, j) { r.classList.toggle("focused", j === focused); });
        rows[focused].scrollIntoView({ block: "nearest" });
        if (preview) BV.theme.applyById(BV.theme.themes[focused].id, false);
      }

      var modal = BV.modal("select theme", body, {
        onClose: function () {
          /* if user escaped without choosing, restore the persisted theme */
          if (!chosen) BV.theme.applyById(startId, false);
        },
        onKey: function (e, close) {
          if (e.key === "ArrowDown" || e.key === "j") { setFocus(focused + 1, true); return true; }
          if (e.key === "ArrowUp" || e.key === "k") { setFocus(focused - 1, true); return true; }
          if (e.key === "Enter") {
            chosen = true;
            BV.theme.applyById(BV.theme.themes[focused].id, true);
            close();
            return true;
          }
          return false;
        },
      });
      setFocus(focused, false);
    },
  };
})();
