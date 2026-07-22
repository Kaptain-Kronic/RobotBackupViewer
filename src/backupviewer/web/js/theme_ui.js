/* theme_ui.js - the 🎨 theme window: everything about how the app LOOKS in
   one place, split into two tabs like a tiny settings app of its own.

   - themes:    the category accordion that used to live in BV.theme.picker()
                (hover previews, click commits, j/k/enter, edit/delete on
                custom themes). Moved here wholesale; the old standalone
                picker modal is gone.
   - customize: the appearance + text & scale rows that used to crowd the ⚙
                settings modal, plus the background effect with its
                intensity/size sliders.

   The ⚙ modal keeps only app behavior (3d view, library folder); this
   window owns presentation. Opens from the topbar 🎨 button or `t`. */
(function () {
  "use strict";

  /* display order of category sections; anything unlisted slots in just
     before Custom, and Custom is always last (it's where you make your own) */
  var CAT_ORDER = ["MonkeyType", "Sports", "Cyberpunk 2077", "Vibes", "Custom"];
  /* attribution shown under each pack's section header */
  var CREDITS = {
    "MonkeyType": "themes adapted from monkeytype.com",
    "Sports": "team & university colors © their respective owners",
    "Cyberpunk 2077": "inspired by Cyberpunk 2077 — © CD Projekt Red",
    "Vibes": "inspired by retro-futuristic gradient art",
    "Custom": "your own — saved as shareable files",
  };
  var FX_CREDIT = "odysseus effects inspired by pewdiepie's odysseus — AGPL-3.0, combined per GPLv3 §13";

  /* a theme's pack; legacy themes with no category fall under MonkeyType */
  function catOf(t) { return t.category || "MonkeyType"; }

  BV.themeUI = {
    /* tab = "themes" (default) | "customize" */
    open: function (tab) {
      if (!BV.theme.themes.length) { BV.toast("no themes loaded"); return; }
      var settings = BV.state.settings || {};
      var activeTab = tab === "customize" ? "customize" : "themes";

      /* the theme committed when the window opened / last clicked; hover
         previews float on top of it and closing settles back onto it */
      var committedId = BV.theme.activeId;
      var skipRestore = false;   /* the edit-theme hop owns the restore then */
      var themesTab = null;      /* {onKey} while the themes tab is up */

      var body = BV.el("div");
      var seg = BV.el("div", { class: "seg theme-tabs" });
      var slot = BV.el("div");
      body.appendChild(seg);
      body.appendChild(slot);

      function switchTab(next) {
        if (activeTab === "themes" && next !== "themes") {
          BV.theme.applyById(committedId, false);   /* drop any hover preview */
        }
        activeTab = next;
        themesTab = null;
        seg.querySelectorAll("button").forEach(function (b) {
          b.classList.toggle("active", b.dataset.tab === next);
        });
        slot.innerHTML = "";
        slot.appendChild(next === "themes" ? buildThemes() : buildCustomize());
      }
      ["themes", "customize"].forEach(function (id) {
        var b = BV.el("button", { class: id === activeTab ? "active" : "" }, id);
        b.dataset.tab = id;
        b.addEventListener("click", function () { switchTab(id); });
        seg.appendChild(b);
      });

      /* ---- themes tab: the category accordion ---- */
      function buildThemes() {
        var all = BV.theme.themes;
        var acc = BV.el("div", { class: "theme-acc" });
        var built = false;   /* guards the initial programmatic open from persisting */
        var focusEl = null;

        var present = [];
        all.forEach(function (t) { var c = catOf(t); if (present.indexOf(c) < 0) present.push(c); });
        if (present.indexOf("Custom") < 0) present.push("Custom");
        present.sort(function (a, b) {
          var ia = CAT_ORDER.indexOf(a), ib = CAT_ORDER.indexOf(b);
          if (ia < 0) ia = CAT_ORDER.length - 1.5;
          if (ib < 0) ib = CAT_ORDER.length - 1.5;
          return ia - ib;
        });

        /* open the active theme's category + the last-expanded one; on first
           run (no custom themes yet) open Custom so "+ new" is visible */
        var activeCat = catOf(all.find(function (t) { return t.id === committedId; }) || {});
        var openSet = {};
        openSet[activeCat] = true;
        if (settings.picker_cat) openSet[settings.picker_cat] = true;
        if (!all.some(function (t) { return t.user; })) openSet.Custom = true;

        function visRows() {
          return Array.prototype.slice.call(
            acc.querySelectorAll(".bv-collapsible.open > .bv-collapse-body .opt-row[data-theme-id]"));
        }
        function setFocus(el, preview) {
          if (focusEl) focusEl.classList.remove("focused");
          focusEl = el || null;
          if (!focusEl) return;
          focusEl.classList.add("focused");
          focusEl.scrollIntoView({ block: "nearest" });
          if (preview && focusEl.dataset.themeId) BV.theme.applyById(focusEl.dataset.themeId, false);
        }
        function moveFocus(delta) {
          var rows = visRows();
          if (!rows.length) return;
          var i = rows.indexOf(focusEl);
          if (i < 0) { setFocus(rows[0], true); return; }
          setFocus(rows[(i + delta + rows.length) % rows.length], true);
        }

        function swatchHtml(t) {
          return ["bg", "accent", "text", "sub"].map(function (k) {
            return '<span class="swatch" style="background:' + BV.esc(t.colors[k] || "#000") + '"></span>';
          }).join("");
        }

        function makeRow(t) {
          var row = BV.el("div", { class: "opt-row" + (t.id === committedId ? " sel" : "") },
            '<span class="name">' + BV.esc(t.name || t.id) + "</span>" +
            '<span class="swatches">' + swatchHtml(t) + "</span>");
          row.dataset.themeId = t.id;
          row.addEventListener("mouseenter", function () { setFocus(row, false); BV.theme.applyById(t.id, false); });
          row.addEventListener("click", function () {
            committedId = t.id;
            BV.theme.applyById(t.id, true);
            var prev = acc.querySelector(".opt-row.sel");
            if (prev) prev.classList.remove("sel");
            row.classList.add("sel");
            BV.toast(t.name || t.id);
          });
          if (t.user) {
            var edit = BV.el("button", { class: "opt-edit", title: "edit (e)" }, "✎");
            edit.addEventListener("click", function (e) {
              e.stopPropagation();
              skipRestore = true; modal.close();
              BV.theme.editTheme(t, committedId);
            });
            row.appendChild(edit);
            var del = BV.el("button", { class: "opt-del", title: "delete" }, "🗑");
            del.addEventListener("click", function (e) {
              e.stopPropagation();
              BV.api.call("delete_user_theme", t.id).then(function () {
                BV.theme.themes = BV.theme.themes.filter(function (x) { return x.id !== t.id; });
                if (focusEl === row) focusEl = null;
                row.remove();
                if (BV.theme.activeId === t.id || committedId === t.id) {
                  committedId = "serika_dark";
                  BV.theme.applyById("serika_dark", true);
                }
                BV.toast("deleted " + (t.name || t.id));
              }).catch(function () { BV.toast("delete failed"); });
            });
            row.appendChild(del);
          }
          return row;
        }

        present.forEach(function (cat) {
          var list = all.filter(function (t) { return catOf(t) === cat; });
          var node = BV.el("div", { class: "acc-sec" });
          var head = BV.el("div", { class: "acc-head" },
            '<span class="acc-name">' + BV.esc(cat) + "</span>" +
            '<span class="acc-count">' + list.length + "</span>");
          var bodyEl = BV.el("div", { class: "acc-body" });

          if (cat === "Custom") {
            var reveal = BV.el("button", { class: "acc-reveal", title: "open themes folder" }, "📁");
            reveal.addEventListener("click", function (e) {
              e.stopPropagation();
              BV.api.call("reveal_themes_dir").catch(function () {});
            });
            head.appendChild(reveal);
            var add = BV.el("div", { class: "opt-row opt-new" }, '<span class="name">＋ new custom theme</span>');
            add.addEventListener("click", function () {
              skipRestore = true; modal.close();
              BV.theme.editTheme(null, committedId);
            });
            bodyEl.appendChild(add);
          }

          list.forEach(function (t) { bodyEl.appendChild(makeRow(t)); });
          if (cat === "Custom" && !list.length) {
            bodyEl.appendChild(BV.el("div", { class: "acc-empty" }, "no custom themes yet — make one ↑"));
          }
          if (CREDITS[cat]) bodyEl.appendChild(BV.el("div", { class: "acc-credit" }, BV.esc(CREDITS[cat])));

          node.appendChild(head);
          node.appendChild(bodyEl);
          BV.collapsible(node, head, bodyEl, {
            open: !!openSet[cat],
            onToggle: function (isOpen) {
              if (!built) return;
              if (isOpen) {
                settings.picker_cat = cat;
                BV.api.call("set_setting", "picker_cat", cat).catch(function () {});
              }
              setFocus(visRows()[0], false);
            },
          });
          acc.appendChild(node);
        });
        built = true;

        themesTab = {
          onKey: function (e, close) {
            if (e.key === "ArrowDown" || e.key === "j") { moveFocus(1); return true; }
            if (e.key === "ArrowUp" || e.key === "k") { moveFocus(-1); return true; }
            if (e.key === "Enter") {
              if (focusEl && focusEl.dataset.themeId) {
                committedId = focusEl.dataset.themeId;
                BV.theme.applyById(committedId, true);
                close();
              }
              return true;
            }
            if (e.key === "e" && focusEl && focusEl.dataset.themeId) {
              var t = all.find(function (x) { return x.id === focusEl.dataset.themeId; });
              if (t && t.user) { skipRestore = true; close(); BV.theme.editTheme(t, committedId); return true; }
            }
            return false;
          },
        };

        setTimeout(function () {
          var sel = acc.querySelector('.opt-row[data-theme-id="' + committedId + '"]');
          setFocus(sel && sel.offsetParent !== null ? sel : visRows()[0], false);
        }, 0);
        return acc;
      }

      /* ---- customize tab: appearance / text & scale / background ---- */
      function buildCustomize() {
        var wrap = BV.el("div");
        var P = BV.uiPrefs;

        function section(title) {
          wrap.appendChild(BV.el("div", { class: "set-head" }, BV.esc(title)));
        }
        function segRow(label, values, current, fmt, onPick) {
          var rowEl = BV.el("div", { class: "set-row" });
          rowEl.appendChild(BV.el("span", { class: "name" }, BV.esc(label)));
          var segEl = BV.el("div", { class: "seg" });
          values.forEach(function (v) {
            var b = BV.el("button", { class: v === current ? "active" : "" }, fmt(v));
            b.addEventListener("click", function () {
              segEl.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
              b.classList.add("active");
              onPick(v);
            });
            segEl.appendChild(b);
          });
          rowEl.appendChild(segEl);
          wrap.appendChild(rowEl);
        }
        function pref(key, v) {
          settings[key] = v;
          P.apply(settings);
          BV.api.call("set_setting", key, v).catch(function () {});
        }

        section("appearance");
        segRow("font", P.FONT_OPTIONS.map(function (o) { return o.id; }),
          settings.font_family || P.DEFAULT_FONT_FAMILY,
          function (id) {
            var o = P.FONT_OPTIONS.find(function (x) { return x.id === id; });
            return o ? o.label : id;
          },
          function (id) { pref("font_family", id); });
        segRow("borders", [true, false], settings.edges !== false,
          function (v) { return v ? "on" : "off"; },
          function (v) { pref("edges", v); });

        section("text & scale");
        segRow("text size", P.FONT_SIZES, settings.font_size || P.DEFAULT_FONT,
          function (v) { return v + "px"; },
          function (v) { pref("font_size", v); });
        segRow("chrome scale", P.CHROME_SCALES, P.chromeScale(settings),
          function (v) { return Math.round(v * 100) + "%"; },
          function (v) { pref("chrome_scale", v); });

        section("background");
        var fxRow = BV.el("div", { class: "set-row" });
        fxRow.appendChild(BV.el("span", { class: "name" }, "effect"));
        var fxBtn = BV.el("button", { class: "btn fx-pick" }, BV.esc(BV.bgfx.activeName()) + " ▾");
        fxRow.appendChild(fxBtn);
        wrap.appendChild(fxRow);
        fxBtn.addEventListener("click", function () {
          BV.menu(fxBtn, BV.bgfx.EFFECTS.map(function (t) {
            return {
              label: t.name + (t.id === BV.bgfx.activeId ? "  ✓" : ""),
              onClick: function () {
                BV.bgfx.set(t.id, true);
                fxBtn.textContent = t.name + " ▾";
              },
            };
          }));
        });

        function sliderRow(label, min, max, step, value, onInput) {
          var rowEl = BV.el("div", { class: "set-row" });
          rowEl.appendChild(BV.el("span", { class: "name" }, BV.esc(label)));
          var holder = BV.el("div", { class: "range-wrap" });
          var val = BV.el("span", { class: "range-val" }, Math.round(value * 100) + "%");
          var input = BV.el("input", {
            type: "range", min: String(min), max: String(max), step: String(step),
            value: String(Math.round(value * 100)),
            oninput: function (e) {
              var v = Number(e.target.value) / 100;
              val.textContent = e.target.value + "%";
              onInput(v);
            },
          });
          holder.appendChild(input);
          holder.appendChild(val);
          rowEl.appendChild(holder);
          wrap.appendChild(rowEl);
        }
        sliderRow("intensity", 10, 100, 5, BV.bgfx.intensity,
          function (v) { BV.bgfx.tune({ intensity: v }, true); });
        sliderRow("size", 50, 200, 10, BV.bgfx.size,
          function (v) { BV.bgfx.tune({ size: v }, true); });
        wrap.appendChild(BV.el("div", { class: "acc-credit" }, BV.esc(FX_CREDIT)));

        return wrap;
      }

      var modal = BV.modal("theme", body, {
        onClose: function () {
          /* settle back onto the last committed theme unless the edit hop
             (which restores on its own terms) took over */
          if (!skipRestore) BV.theme.applyById(committedId, false);
        },
        onKey: function (e, close) {
          if (activeTab === "themes" && themesTab) return themesTab.onKey(e, close);
          return false;
        },
      });
      modal.el.classList.add("theme-win");

      switchTab(activeTab);
    },
  };
})();
