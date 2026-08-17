/* theme.js - MonkeyType-style theming: a theme is ~9 colors applied as CSS vars.
   Bundled packs are read-only; user themes are editable files under the Custom category.
   The theme BROWSER (accordion, categories, credits) lives in theme_ui.js — this file
   is the data + apply layer plus the color editor. */
(function () {
  "use strict";

  var VAR_MAP = {
    bg: "--bg", bg2: "--bg2", sub: "--sub", subAlt: "--sub-alt",
    text: "--text", accent: "--accent", error: "--error", ok: "--ok", warn: "--warn",
  };

  var CUSTOM_CAT = "Custom";

  /* ---- color math (only the custom-theme editor uses these) ----
     No color lib ships with the app; these are tiny pure helpers, zero deps. */

  /* the ONE parse core: optional #, 3/6/8 digits → lowercase #rrggbb, or null.
     Every hex reader here goes through this — the file's own BV.hexRgb comment
     below records what scattered copies of this regex cost once already. */
  function parseHex(text) {
    var h = String(text == null ? "" : text).trim().toLowerCase();
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(h);
    if (!m) return null;
    var v = m[1];
    if (v.length === 3) v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2];
    return "#" + v.slice(0, 6); /* drop any alpha */
  }

  /* Coerce any hex into lowercase #rrggbb. <input type=color> silently snaps anything
     that isn't exactly 6-digit lowercase to #000000, so normalize before binding. */
  function normalizeHex(hex) { return parseHex(hex) || "#000000"; }

  /* What a human pastes into the editor's hex field: same grammar, plus O/o
     read as zero — O is not a hex digit, so the reading is unambiguous, and
     codes hand-copied out of chat or screenshots carry it constantly. Null
     when the text is not a color (yet) so the caller can wait instead of
     inventing one. The replace runs before parseHex lowercases, so it must
     catch uppercase O itself. */
  function readHex(text) {
    return parseHex(String(text == null ? "" : text).replace(/o/gi, "0"));
  }

  function hexToRgb(hex) {
    var h = normalizeHex(hex).slice(1);
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  /* The ONE hex reader for everything that paints from a theme variable.
     bgfx and the glass slider each carried their own 3-or-6-digit regex, so an
     8-digit (alpha) theme color missed and they painted a HARDCODED default -
     the app's own rule is that colors derive from the variables, never a
     literal. Returns [r,g,b], or null when the text really is not a hex so the
     caller can skip painting instead of inventing a color. */
  BV.hexRgb = function (str) {
    var h = String(str == null ? "" : str).trim().toLowerCase();
    if (!/^#?([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/.test(h)) return null;
    return hexToRgb(h);
  };

  /* the computed value of a CSS custom property, as [r,g,b] or null */
  BV.varRgb = function (name) {
    return BV.hexRgb(getComputedStyle(document.documentElement).getPropertyValue(name));
  };

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
      /* every row's controls, keyed by color: a draft restore or a bg-derive
         must repaint ALL of them (the old advanced-only sync left the four
         main color inputs showing stale values after a restore) */
      var inputs = {};

      /* The saved palette: app-level swatches (settings.json), NOT part of
         the theme being edited — brand/plant colors you reuse across themes.
         Adding or removing a swatch saves immediately and never dirties the
         editor; APPLYING one edits the theme, so that path goes through
         onPick like any other pick. */
      var pal = (((BV.state.settings || {}).palette) || [])
        .filter(function (h) { return BV.hexRgb(h); }).map(normalizeHex);
      var palTarget = "bg";
      function savePalette() {
        if (BV.state.settings) BV.state.settings.palette = pal.slice();
        BV.api.call("set_setting", "palette", pal.slice()).catch(function () {});
      }
      function setTarget(key) {
        palTarget = key;
        Object.keys(inputs).forEach(function (k) {
          inputs[k].row.classList.toggle("pal-target", k === key);
        });
      }

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
        /* disarm any pending debounced write FIRST — a save inside the 500ms
           window otherwise clears the draft and then the timer fires and
           resurrects it, so every quick save grew a ghost "unsaved edits" bar */
        draftWrite.cancel();
        if (BV.state.settings) BV.state.settings.theme_draft = null;
        BV.api.call("set_setting", "theme_draft", null).catch(function () {});
      }

      function applyWorking() { BV.theme.apply({ id: "__preview__", colors: working }); }
      function syncInputs() {
        Object.keys(inputs).forEach(function (k) {
          inputs[k].color.value = normalizeHex(working[k]);
          /* never rewrite the hex field mid-typing — blur/Enter canonicalizes */
          if (document.activeElement !== inputs[k].hex) {
            inputs[k].hex.value = normalizeHex(working[k]);
            inputs[k].hex.classList.remove("bad");
          }
        });
      }
      function onPick(key, val) {
        working[key] = normalizeHex(val);
        if (ADVANCED_SET[key]) overridden[key] = true;
        deriveAux(working, overridden);
        applyWorking();
        syncInputs();
        updateContrast();
        dirty = true;
        draftWrite();
      }
      function colorRow(def) {
        var rowEl = BV.el("div", { class: "editor-row" });
        rowEl.appendChild(BV.el("span", { class: "name" }, BV.esc(def.label)));
        var hex = BV.el("input", {
          type: "text", class: "editor-hex", spellcheck: "false",
          value: normalizeHex(working[def.key]),
        });
        /* live-apply only a full 6-digit code: a 3-digit code is valid the
           moment you have typed half of a 6-digit one (#0d3 inside #0d3b3e),
           so short codes wait for Enter/blur instead of flashing a wrong color */
        hex.addEventListener("input", function () {
          var v = readHex(hex.value);
          /* red only when the text can no longer BECOME a code — every honest
             prefix stays neutral; change/blur judges anything unfinished */
          hex.classList.toggle("bad", !v && !/^#?[0-9a-fo]{0,8}$/i.test(hex.value.trim()));
          if (v && hex.value.trim().replace(/^#/, "").length >= 6) onPick(def.key, v);
        });
        hex.addEventListener("change", function () {
          var v = readHex(hex.value);
          if (v) onPick(def.key, v);
          hex.value = normalizeHex(working[def.key]);   /* canonical, or snap back */
          hex.classList.remove("bad");
        });
        var input = BV.el("input", {
          type: "color",
          value: normalizeHex(working[def.key]),
          oninput: function (e) { onPick(def.key, e.target.value); },
        });
        inputs[def.key] = { color: input, hex: hex, row: rowEl };
        /* touching a row makes it the palette's target */
        rowEl.addEventListener("mousedown", function () { setTarget(def.key); });
        rowEl.addEventListener("focusin", function () { setTarget(def.key); });
        rowEl.appendChild(hex);
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
          applyWorking(); syncInputs(); updateContrast();
          draftBar.remove();
        });
        dropBtn.addEventListener("click", function () { clearDraft(); draftBar.remove(); });
        draftBar.appendChild(restoreBtn);
        draftBar.appendChild(dropBtn);
        body.insertBefore(draftBar, body.firstChild);
      }

      MAINS.forEach(function (d) { body.appendChild(colorRow(d)); });
      body.appendChild(hint);

      /* the saved-palette strip: click a swatch to paint the highlighted row,
         ＋ keeps that row's current color, × forgets a swatch */
      var palRow = BV.el("div", { class: "editor-row pal-row" });
      palRow.appendChild(BV.el("span", { class: "name" }, "palette"));
      var strip = BV.el("div", { class: "pal-strip" });
      palRow.appendChild(strip);
      function paintPalette() {
        strip.innerHTML = "";
        pal.forEach(function (hx, i) {
          var sw = BV.el("button", { class: "pal-swatch", title: hx + " — apply to the highlighted row" });
          sw.style.background = hx;
          sw.addEventListener("click", function () { onPick(palTarget, hx); });
          var x = BV.el("span", { class: "pal-x", title: "remove " + hx }, "×");
          x.addEventListener("click", function (e) {
            e.stopPropagation();
            /* the repaint shifts the next swatch's × under the pointer, so the
               second click of a double-click would delete the neighbor too */
            if (e.detail > 1) return;
            pal.splice(i, 1);
            savePalette();
            paintPalette();
          });
          sw.appendChild(x);
          strip.appendChild(sw);
        });
        var add = BV.el("button", { class: "pal-add", title: "save the highlighted row's color to the palette" }, "＋");
        add.addEventListener("click", function () {
          var hx = normalizeHex(working[palTarget]);
          if (pal.indexOf(hx) >= 0) { BV.toast("already in the palette"); return; }
          pal.push(hx);
          savePalette();
          paintPalette();
        });
        strip.appendChild(add);
        if (!pal.length) {
          strip.appendChild(BV.el("span", { class: "pal-hint" }, "＋ saves the highlighted color"));
        }
      }
      paintPalette();
      body.appendChild(palRow);

      var advNode = BV.el("div");
      var advHead = BV.el("div", { class: "editor-adv-head" }, "advanced");
      var advBody = BV.el("div");
      ADV.forEach(function (d) { advBody.appendChild(colorRow(d)); });
      advNode.appendChild(advHead);
      advNode.appendChild(advBody);
      body.appendChild(advNode);
      BV.collapsible(advNode, advHead, advBody, { open: false, onToggle: function (open) {
        /* a hidden row can't be the palette's target — the swatches would
           paint a color nobody can see move */
        if (!open && ADVANCED_SET[palTarget]) setTarget("bg");
      } });

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

      var guard = BV.dirtyGuard(function () { return dirty && !committed; }, "theme edits");
      var modal = BV.modal(isNew ? "new custom theme" : "edit custom theme", body, {
        beforeClose: function () {
          /* backdrop-mousedown and Esc both outrun the focused hex field's
             blur→change, so a pending short code would vanish unguarded —
             flush it first and the guard can see the edit */
          var ae = document.activeElement;
          Object.keys(inputs).forEach(function (k) {
            if (inputs[k].hex === ae) {
              var v = readHex(ae.value);
              if (v && v !== normalizeHex(working[k])) onPick(k, v);
            }
          });
          return guard();
        },
        onClose: function () { if (!committed) BV.theme.applyById(restoreId, false); },
      });

      /* preview immediately. For a new theme deriveAux already ran; for an edit we keep the
         saved colors as-is (no derive) so nothing resets. */
      applyWorking();
      syncInputs();
      setTarget(palTarget);
      updateContrast();
      if (isNew) setTimeout(function () { try { nameInput.focus(); } catch (e) {} }, 0);
    },
  };
})();
