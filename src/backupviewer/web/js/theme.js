/* theme.js - MonkeyType-style theming: a theme is ~9 colors applied as CSS vars.
   Bundled packs are read-only; user themes are editable files under the Custom category. */
(function () {
  "use strict";

  var VAR_MAP = {
    bg: "--bg", bg2: "--bg2", sub: "--sub", subAlt: "--sub-alt",
    text: "--text", accent: "--accent", error: "--error", ok: "--ok", warn: "--warn",
  };

  var CUSTOM_CAT = "Custom";
  /* display order of category sections; anything unlisted slots in just before Custom,
     and Custom is always last (it's where you make your own) */
  var CAT_ORDER = ["MonkeyType", "Sports", "Cyberpunk 2077", "Vibes", CUSTOM_CAT];
  /* attribution shown under each pack's section header */
  var CREDITS = {
    "MonkeyType": "themes adapted from monkeytype.com",
    "Sports": "team & university colors © their respective owners",
    "Cyberpunk 2077": "inspired by Cyberpunk 2077 — © CD Projekt Red",
    "Vibes": "inspired by retro-futuristic gradient art",
    "Custom": "your own — saved as shareable files",
  };

  /* a theme's pack; legacy themes with no category fall under MonkeyType */
  function catOf(t) { return t.category || "MonkeyType"; }

  /* ---- color math (only the custom-theme editor uses these) ----
     No color lib ships with the app; these are tiny pure helpers, zero deps. */

  /* Coerce any hex into lowercase #rrggbb. <input type=color> silently snaps anything
     that isn't exactly 6-digit lowercase to #000000, so normalize before binding. */
  function normalizeHex(hex) {
    var h = String(hex == null ? "" : hex).trim().toLowerCase();
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(h);
    if (!m) return "#000000";
    var v = m[1];
    if (v.length === 3) v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2];
    return "#" + v.slice(0, 6); /* drop any alpha */
  }

  function hexToRgb(hex) {
    var h = normalizeHex(hex).slice(1);
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  /* linear per-channel blend; t is the fraction of b mixed into a */
  function mixHex(a, b, t) {
    var ra = hexToRgb(a), rb = hexToRgb(b);
    var out = ra.map(function (c, i) {
      var v = Math.round(c * (1 - t) + rb[i] * t);
      return ("0" + Math.max(0, Math.min(255, v)).toString(16)).slice(-2);
    });
    return "#" + out.join("");
  }

  /* WCAG relative luminance + contrast ratio (for the non-blocking readability hint) */
  function relLum(hex) {
    var lin = hexToRgb(hex).map(function (c) {
      c = c / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  }
  function contrast(h1, h2) {
    var l1 = relLum(h1), l2 = relLum(h2);
    var hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  /* bg2/subAlt as a function of bg unless the user overrode them in "advanced".
     bg2 recedes (darken) so panels read as a layer below bg on light AND dark seeds. */
  function derivedBg2(working) { return mixHex(working.bg, "#000000", 0.06); }
  function derivedSubAlt(working) { return mixHex(working.bg, working.sub, 0.35); }
  function deriveAux(working, overridden) {
    if (!overridden.bg2) working.bg2 = derivedBg2(working);
    if (!overridden.subAlt) working.subAlt = derivedSubAlt(working);
  }

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

    /* The theme picker: a collapsible accordion, one section per category. The first view
       is just the category headers; expand one to see its themes. The Custom section holds
       your editable themes plus a "new" entry and a reveal-folder shortcut. */
    picker: function () {
      if (!BV.theme.themes.length) { BV.toast("no themes loaded"); return; }
      var all = BV.theme.themes;
      var startId = BV.theme.activeId;
      var chosen = false;
      var settings = BV.state.settings || {};
      var built = false;   /* guards the initial programmatic open from persisting */

      var body = BV.el("div", { class: "theme-acc" });

      /* categories present, ordered by CAT_ORDER (unknowns just before Custom) */
      var present = [];
      all.forEach(function (t) { var c = catOf(t); if (present.indexOf(c) < 0) present.push(c); });
      /* always offer Custom, even with no user themes yet - it holds the "+ new" entry,
         so there's somewhere to make your first one */
      if (present.indexOf(CUSTOM_CAT) < 0) present.push(CUSTOM_CAT);
      present.sort(function (a, b) {
        var ia = CAT_ORDER.indexOf(a), ib = CAT_ORDER.indexOf(b);
        if (ia < 0) ia = CAT_ORDER.length - 1.5;
        if (ib < 0) ib = CAT_ORDER.length - 1.5;
        return ia - ib;
      });

      /* which sections open on launch: the active theme's category, plus the one you last
         expanded (remembered) - so reopening lands "where you were". On first run (no custom
         themes yet) open Custom too, so "+ new" is visible without hunting. */
      var activeCat = catOf(all.find(function (t) { return t.id === startId; }) || {});
      var openSet = {};
      openSet[activeCat] = true;
      if (settings.picker_cat) openSet[settings.picker_cat] = true;
      if (!all.some(function (t) { return t.user; })) openSet[CUSTOM_CAT] = true;

      var focusEl = null;
      function visRows() {
        return Array.prototype.slice.call(
          body.querySelectorAll(".bv-collapsible.open > .bv-collapse-body .opt-row[data-theme-id]"));
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
        var row = BV.el("div", { class: "opt-row" + (t.id === startId ? " sel" : "") },
          '<span class="name">' + BV.esc(t.name || t.id) + "</span>" +
          '<span class="swatches">' + swatchHtml(t) + "</span>");
        row.dataset.themeId = t.id;
        row.addEventListener("mouseenter", function () { setFocus(row, false); BV.theme.applyById(t.id, false); });
        row.addEventListener("click", function () {
          chosen = true; BV.theme.applyById(t.id, true); modal.close();
        });
        if (t.user) {
          var edit = BV.el("button", { class: "opt-edit", title: "edit (e)" }, "✎");
          edit.addEventListener("click", function (e) {
            e.stopPropagation();
            chosen = true; modal.close();
            BV.theme.editTheme(t, startId);
          });
          row.appendChild(edit);
          var del = BV.el("button", { class: "opt-del", title: "delete" }, "🗑");
          del.addEventListener("click", function (e) {
            e.stopPropagation();
            BV.api.call("delete_user_theme", t.id).then(function () {
              BV.theme.themes = BV.theme.themes.filter(function (x) { return x.id !== t.id; });
              if (focusEl === row) focusEl = null;
              row.remove();
              if (BV.theme.activeId === t.id || startId === t.id) {
                startId = "serika_dark";
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

        if (cat === CUSTOM_CAT) {
          var reveal = BV.el("button", { class: "acc-reveal", title: "open themes folder" }, "📁");
          reveal.addEventListener("click", function (e) {
            e.stopPropagation();
            BV.api.call("reveal_themes_dir").catch(function () {});
          });
          head.appendChild(reveal);
          var add = BV.el("div", { class: "opt-row opt-new" }, '<span class="name">＋ new custom theme</span>');
          add.addEventListener("click", function () {
            chosen = true; modal.close();
            BV.theme.editTheme(null, startId);
          });
          bodyEl.appendChild(add);
        }

        list.forEach(function (t) { bodyEl.appendChild(makeRow(t)); });
        if (cat === CUSTOM_CAT && !list.length) {
          bodyEl.appendChild(BV.el("div", { class: "acc-empty" }, "no custom themes yet — make one ↑"));
        }

        var credit = CREDITS[cat];
        if (credit) bodyEl.appendChild(BV.el("div", { class: "acc-credit" }, BV.esc(credit)));

        node.appendChild(head);
        node.appendChild(bodyEl);
        BV.collapsible(node, head, bodyEl, {
          open: !!openSet[cat],
          onToggle: function (isOpen) {
            if (!built) return;   /* ignore the open() that fires during construction */
            if (isOpen) {
              settings.picker_cat = cat;
              BV.api.call("set_setting", "picker_cat", cat).catch(function () {});
            }
            setFocus(visRows()[0], false);
          },
        });
        body.appendChild(node);
      });
      built = true;

      var modal = BV.modal("select theme", body, {
        onClose: function () { if (!chosen) BV.theme.applyById(startId, false); },
        onKey: function (e, close) {
          if (e.key === "ArrowDown" || e.key === "j") { moveFocus(1); return true; }
          if (e.key === "ArrowUp" || e.key === "k") { moveFocus(-1); return true; }
          if (e.key === "Enter") {
            if (focusEl && focusEl.dataset.themeId) {
              chosen = true; BV.theme.applyById(focusEl.dataset.themeId, true); close();
            }
            return true;
          }
          if (e.key === "e" && focusEl && focusEl.dataset.themeId) {
            var t = all.find(function (x) { return x.id === focusEl.dataset.themeId; });
            if (t && t.user) { chosen = true; close(); BV.theme.editTheme(t, startId); return true; }
          }
          return false;
        },
      });

      /* anchor keyboard focus on the active theme's row if it's in an open section */
      var sel = body.querySelector('.opt-row[data-theme-id="' + (startId || "") + '"]');
      setFocus(sel && sel.offsetParent !== null ? sel : visRows()[0], false);
    },

    /* The live color editor. theme = an existing user theme to edit, or null to create a
       new one (seeded from the active theme). restoreId is the theme to fall back to on
       cancel. Preview is live; only Save writes the file. */
    editTheme: function (theme, restoreId) {
      restoreId = restoreId || BV.theme.activeId;
      var isNew = !theme;
      var base = theme ? theme.colors
        : ((BV.theme.themes.find(function (t) { return t.id === BV.theme.activeId; })
            || BV.theme.themes[0] || {}).colors || {});
      var prevId = theme ? theme.id : null;

      var MAINS = [
        { key: "bg", label: "background" }, { key: "accent", label: "accent" },
        { key: "text", label: "text" }, { key: "sub", label: "sub" },
      ];
      var ADV = [
        { key: "bg2", label: "background 2" }, { key: "subAlt", label: "sub alt" },
        { key: "error", label: "error" }, { key: "ok", label: "ok" }, { key: "warn", label: "warn" },
      ];
      var ADVANCED_SET = { bg2: 1, subAlt: 1, error: 1, ok: 1, warn: 1 };

      var working = {};
      Object.keys(VAR_MAP).forEach(function (k) { working[k] = normalizeHex(base[k]); });
      var overridden = {};
      if (isNew) {
        deriveAux(working, overridden);   /* a fresh theme's aux colors track bg as you edit */
      } else {
        /* Edit: keep the saved colors verbatim, and treat any aux that ISN'T the plain
           derived value as hand-set, so a later bg change won't silently overwrite it.
           (This is the fix for advanced colors resetting when you reopen a theme.) */
        if (working.bg2 !== derivedBg2(working)) overridden.bg2 = true;
        if (working.subAlt !== derivedSubAlt(working)) overridden.subAlt = true;
      }
      var committed = false;
      var dirty = false;
      var advInputs = {};

      /* draft autosave: every edit persists a crash-proof draft to settings.json
         (debounced); cleared on save or when the user declines a restore. This
         is the net under the dirty-guard — even a power cut can't eat a theme. */
      function writeDraft() {
        var d = { isNew: isNew, prevId: prevId, name: nameInput.value || "",
                  colors: working, overridden: overridden };
        if (BV.state.settings) BV.state.settings.theme_draft = d;
        BV.api.call("set_setting", "theme_draft", d).catch(function () {});
      }
      var draftWrite = BV.debounce(writeDraft, 500);
      function clearDraft() {
        if (BV.state.settings) BV.state.settings.theme_draft = null;
        BV.api.call("set_setting", "theme_draft", null).catch(function () {});
      }

      function applyWorking() { BV.theme.apply({ id: "__preview__", colors: working }); }
      function syncAdvancedInputs() {
        Object.keys(advInputs).forEach(function (k) { advInputs[k].value = normalizeHex(working[k]); });
      }
      function onPick(key, val) {
        working[key] = normalizeHex(val);
        if (ADVANCED_SET[key]) overridden[key] = true;
        deriveAux(working, overridden);
        applyWorking();
        syncAdvancedInputs();
        updateContrast();
        dirty = true;
        draftWrite();
      }
      function colorRow(def, isAdv) {
        var rowEl = BV.el("div", { class: "editor-row" });
        rowEl.appendChild(BV.el("span", { class: "name" }, BV.esc(def.label)));
        var input = BV.el("input", {
          type: "color",
          value: normalizeHex(working[def.key]),
          oninput: function (e) { onPick(def.key, e.target.value); },
        });
        if (isAdv) advInputs[def.key] = input;
        rowEl.appendChild(input);
        return rowEl;
      }

      /* non-blocking readability readout - warns on low contrast, never forbids a combo */
      var hint = BV.el("div", { class: "contrast-hint" });
      function pair(label, a, b) {
        var r = contrast(working[a], working[b]);
        var glyph = r >= 4.5 ? "✓" : (r >= 3 ? "~" : "⚠");
        var open = r >= 3 ? "<span>" : '<span class="warn">';
        return open + label + " " + r.toFixed(1) + " " + glyph + "</span>";
      }
      function updateContrast() {
        hint.innerHTML =
          pair("text/bg", "text", "bg") + " · " +
          pair("accent/bg", "accent", "bg") + " · " +
          pair("sub/bg", "sub", "bg") +
          '<span class="note"> — low accent/bg fades the panel edges</span>';
      }

      var body = BV.el("div");

      var nameRow = BV.el("div", { class: "editor-row" });
      nameRow.appendChild(BV.el("span", { class: "name" }, "name"));
      var nameInput = BV.el("input", {
        type: "text", class: "editor-name", placeholder: "theme name",
        value: theme ? (theme.name || "") : "",
      });
      nameInput.addEventListener("input", function () { dirty = true; draftWrite(); });
      nameRow.appendChild(nameInput);
      body.appendChild(nameRow);

      /* offer a leftover draft (crash / discarded session) for THIS editor
         context: the same theme being re-edited, or any new-theme session */
      var draft = (BV.state.settings || {}).theme_draft;
      if (draft && draft.colors && (isNew ? draft.isNew : draft.prevId === prevId)) {
        var draftBar = BV.el("div", { class: "draft-bar" });
        draftBar.appendChild(BV.el("span", null, "unsaved edits from last time" +
          (draft.name ? " (“" + BV.esc(draft.name) + "”)" : "")));
        var restoreBtn = BV.el("button", { class: "btn" }, "restore");
        var dropBtn = BV.el("button", { class: "btn" }, "discard");
        restoreBtn.addEventListener("click", function () {
          Object.keys(VAR_MAP).forEach(function (k) {
            if (draft.colors[k]) working[k] = normalizeHex(draft.colors[k]);
          });
          Object.keys(overridden).forEach(function (k) { delete overridden[k]; });
          Object.keys(draft.overridden || {}).forEach(function (k) { overridden[k] = true; });
          nameInput.value = draft.name || nameInput.value;
          dirty = true;
          applyWorking(); syncAdvancedInputs(); updateContrast();
          draftBar.remove();
        });
        dropBtn.addEventListener("click", function () { clearDraft(); draftBar.remove(); });
        draftBar.appendChild(restoreBtn);
        draftBar.appendChild(dropBtn);
        body.insertBefore(draftBar, body.firstChild);
      }

      MAINS.forEach(function (d) { body.appendChild(colorRow(d, false)); });
      body.appendChild(hint);

      var advNode = BV.el("div");
      var advHead = BV.el("div", { class: "editor-adv-head" }, "advanced");
      var advBody = BV.el("div");
      ADV.forEach(function (d) { advBody.appendChild(colorRow(d, true)); });
      advNode.appendChild(advHead);
      advNode.appendChild(advBody);
      body.appendChild(advNode);
      BV.collapsible(advNode, advHead, advBody, { open: false });

      function save() {
        var nm = (nameInput.value || "").trim();
        if (!nm) { BV.toast("name your theme first"); nameInput.focus(); return; }
        committed = true;
        BV.api.call("save_user_theme", { name: nm, category: CUSTOM_CAT, colors: working }, prevId)
          .then(function (saved) {
            if (!saved || !saved.id) { BV.toast("save failed"); return; }
            var list = BV.theme.themes;
            if (prevId && prevId !== saved.id) {
              list = list.filter(function (t) { return t.id !== prevId; });
            }
            var i = list.findIndex(function (t) { return t.id === saved.id; });
            if (i >= 0) list[i] = saved; else list.push(saved);
            BV.theme.themes = list;
            BV.theme.applyById(saved.id, true);
            clearDraft();                         /* safely on disk — the net comes down */
            BV.toast("saved " + nm);
          })
          .catch(function () { BV.toast("save failed"); });
        modal.close();
      }

      var actions = BV.el("div", { class: "editor-actions" });
      var cancelBtn = BV.el("button", {}, "cancel");
      var saveBtn = BV.el("button", { class: "save" }, "save");
      cancelBtn.addEventListener("click", function () { modal.close(); });
      saveBtn.addEventListener("click", save);
      actions.appendChild(cancelBtn);
      actions.appendChild(saveBtn);
      body.appendChild(actions);

      var modal = BV.modal(isNew ? "new custom theme" : "edit custom theme", body, {
        beforeClose: BV.dirtyGuard(function () { return dirty && !committed; }, "theme edits"),
        onClose: function () { if (!committed) BV.theme.applyById(restoreId, false); },
      });

      /* preview immediately. For a new theme deriveAux already ran; for an edit we keep the
         saved colors as-is (no derive) so nothing resets. */
      applyWorking();
      syncAdvancedInputs();
      updateContrast();
      if (isNew) setTimeout(function () { try { nameInput.focus(); } catch (e) {} }, 0);
    },
  };
})();
