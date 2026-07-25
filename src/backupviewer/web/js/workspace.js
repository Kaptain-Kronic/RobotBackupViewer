/* workspace.js - BV.workspace: the multi-robot edit working set.

   State is MODULE-LEVEL on purpose. BV.tabState is per-backup (state.js
   re-points tabData on every setManifest and dropBucket deletes it when a
   backup tab closes), so a working set kept there would vanish the moment you
   switched robots - and would read empty with no backup open at all, which is
   exactly when the workspace still has to work.

   An entry is identified by {root, file} where `file` is the path RELATIVE to
   the backup root, never the basename: every robot has a MAIN.LS. The map key
   is root + BV.KEYSEP + file (NUL can appear in neither).

   Programs are read and exported through the path-addressed ws_* endpoints, so
   the workspace never opens a session and is not bound by MAX_OPEN_SESSIONS.

   The working set and any unsaved drafts persist to %APPDATA% via set_setting
   (never localStorage), so closing the app does not lose your edits. */
(function () {
  "use strict";

  var MAX_DRAFT_BYTES = 4 * 1024 * 1024;   /* keep settings.json sane */

  var entries = [];    /* [{root, file, name, label}] - insertion order */
  var bufs = {};       /* key -> {text, base, attrs, baseAttrs, posEdits, exported} */
  var loaded = false;

  function key(root, file) { return root + BV.KEYSEP + file; }
  function find(root, file) {
    var k = key(root, file);
    for (var i = 0; i < entries.length; i++) {
      if (key(entries[i].root, entries[i].file) === k) return i;
    }
    return -1;
  }
  function changed() {
    BV.state.emit("workspace", BV.workspace.count());
    save();
  }

  /* ---- persistence ---- */
  var saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var drafts = {}, bytes = 0, dropped = 0;
      Object.keys(bufs).forEach(function (k) {
        var b = bufs[k];
        if (!isDirtyBuf(b)) return;
        var blob = JSON.stringify({ t: b.text, a: b.attrs, p: b.posEdits });
        if (bytes + blob.length > MAX_DRAFT_BYTES) { dropped++; return; }
        bytes += blob.length;
        drafts[k] = { t: b.text, a: b.attrs, p: b.posEdits };
      });
      if (dropped) BV.toast(dropped + " draft(s) too large to remember", 3000);
      BV.api.call("set_setting", "ws_entries", entries).catch(function () {});
      BV.api.call("set_setting", "ws_drafts", drafts).catch(function () {});
    }, 400);
  }

  function isDirtyBuf(b) {
    if (!b) return false;
    /* exported === null means never stamped - a draft restored from
       settings, which is unsaved work by definition */
    if (b.exported === null) return true;
    return JSON.stringify([b.text, b.attrs, b.posEdits]) !== b.exported;
  }
  function stamp(b) { b.exported = JSON.stringify([b.text, b.attrs, b.posEdits]); }

  /* ---- the module ---- */
  BV.workspace = {
    /* rehydrate from settings; safe to call more than once */
    load: function () {
      if (loaded) return;
      loaded = true;
      var s = BV.state.settings || {};
      var saved = s.ws_entries, drafts = s.ws_drafts || {};
      if (Object.prototype.toString.call(saved) === "[object Array]") {
        saved.forEach(function (e) {
          if (e && e.root && e.file) {
            entries.push({ root: e.root, file: e.file,
                           name: e.name || e.file, label: e.label || "" });
          }
        });
      }
      Object.keys(drafts).forEach(function (k) {
        var d = drafts[k];
        if (!d) return;
        /* a draft with no baseline yet: seed lazily, but keep the text so the
           edit survives. base/baseAttrs fill in on first buffer() call. */
        bufs[k] = { text: d.t, base: null, attrs: d.a || {}, baseAttrs: null,
                    posEdits: d.p || {}, exported: null };
      });
      if (entries.length) BV.state.emit("workspace", this.count());
    },

    entries: function () { return entries.slice(); },
    count: function () { return entries.length; },
    has: function (root, file) { return find(root, file) >= 0; },

    /* entry: {root, file, name?, label?} - label is the robot name used for the
       export subfolder and the rail grouping */
    add: function (e) {
      if (!e || !e.root || !e.file) return false;
      if (find(e.root, e.file) >= 0) return false;
      entries.push({ root: e.root, file: e.file,
                     name: e.name || e.file.split("/").pop(),
                     label: e.label || "" });
      changed();
      return true;
    },
    addMany: function (list) {
      var n = 0;
      (list || []).forEach(function (e) { if (BV.workspace.add(e)) n++; });
      return n;
    },
    remove: function (root, file) {
      var i = find(root, file);
      if (i < 0) return false;
      entries.splice(i, 1);
      delete bufs[key(root, file)];
      changed();
      return true;
    },
    clear: function () { entries = []; bufs = {}; changed(); },

    /* grouped for the rail: [{label, root, programs:[entry]}] in insertion order */
    byRobot: function () {
      var order = [], groups = {};
      entries.forEach(function (e) {
        var g = groups[e.root];
        if (!g) {
          g = groups[e.root] = { root: e.root, label: e.label || e.root.split(/[\\/]/).pop(),
                                 programs: [] };
          order.push(g);
        }
        g.programs.push(e);
      });
      return order;
    },

    /* promise of the buffer, seeded from PRISTINE bytes on first use */
    buffer: function (root, file) {
      var k = key(root, file), b = bufs[k];
      if (b && b.base !== null) return Promise.resolve(b);
      return BV.api.call("ws_get_program", root, file).then(function (res) {
        var cur = bufs[k];
        if (cur) {
          /* a restored draft: keep the user's text, fill in the baseline */
          cur.base = res.body;
          cur.baseAttrs = res.attrs;
          cur.positions = res.positions || [];
          if (!cur.attrs || !Object.keys(cur.attrs).length) cur.attrs = copy(res.attrs);
          return cur;
        }
        b = bufs[k] = {
          text: res.body, base: res.body,
          attrs: copy(res.attrs), baseAttrs: res.attrs,
          positions: res.positions || [],
          posEdits: {}, exported: null,
        };
        stamp(b);
        return b;
      });
    },
    peek: function (root, file) { return bufs[key(root, file)] || null; },

    setBody: function (root, file, text) {
      var b = bufs[key(root, file)];
      if (!b) return;
      b.text = text;
      changed();
    },
    setAttr: function (root, file, name, value) {
      var b = bufs[key(root, file)];
      if (!b) return;
      b.attrs[name] = value;
      changed();
    },
    setPos: function (root, file, edit) {
      var b = bufs[key(root, file)];
      if (!b) return;
      var pk = edit.id + "|" + edit.gp + "|" + edit.field;
      if (edit.value === null || edit.value === "") delete b.posEdits[pk];
      else b.posEdits[pk] = edit;
      changed();
    },
    getPos: function (root, file, id, gp, field) {
      var b = bufs[key(root, file)];
      var e = b && b.posEdits[id + "|" + gp + "|" + field];
      return e ? e.value : null;
    },

    dirty: function (root, file) { return isDirtyBuf(bufs[key(root, file)]); },
    dirtyCount: function () {
      return Object.keys(bufs).filter(function (k) { return isDirtyBuf(bufs[k]); }).length;
    },
    anyDirty: function () { return this.dirtyCount() > 0; },
    dirtyRobots: function () {
      var seen = {};
      entries.forEach(function (e) {
        if (isDirtyBuf(bufs[key(e.root, e.file)])) seen[e.root] = 1;
      });
      return Object.keys(seen).length;
    },

    /* ws_export payload: every entry whose buffer differs from PRISTINE.
       Body tokens are built by trimming the common head/tail so untouched
       lines stay byte-exact in the export. */
    edits: function () {
      var out = [];
      entries.forEach(function (e) {
        var b = bufs[key(e.root, e.file)];
        if (!b || b.base === null) return;
        var body = b.text === b.base ? null : tokens(b.base.split("\n"), b.text.split("\n"));
        var attrs = attrDiff(b.attrs, b.baseAttrs);
        var pos = Object.keys(b.posEdits).map(function (k) { return b.posEdits[k]; });
        if (!body && !attrs && !pos.length) return;
        out.push({ root: e.root, file: e.file, label: e.label || "",
                   body: body, attrs: attrs, positions: pos.length ? pos : null });
      });
      return out;
    },

    /* after a successful export the current state becomes the clean baseline;
       the edits STAY (base remains pristine) so a re-export re-applies them */
    markSaved: function () {
      Object.keys(bufs).forEach(function (k) { stamp(bufs[k]); });
      changed();
    },
  };

  /* ---- shared affordance ----
     One button used by the programs list, the program detail view and both
     sides of a compare, so "add to workspace" behaves identically everywhere.
     opts: {label, title, jump (go to #edit after adding), entries()} where
     entries() returns [{root, file, name, label}] at CLICK time (the caller's
     selection can change after the button is built). */
  BV.workspace.button = function (opts) {
    var b = BV.el("button", { class: "btn", title: opts.title || "add to the edit workspace" },
                   opts.label || "+ workspace");
    b.addEventListener("click", function () {
      var list = (opts.entries() || []).filter(function (e) { return e && e.root && e.file; });
      if (!list.length) { BV.toast("nothing to add"); return; }
      var n = BV.workspace.addMany(list);
      var already = list.length - n;
      if (n) {
        BV.toast(n + " program" + (n === 1 ? "" : "s") + " added to the workspace" +
                 (already ? " (" + already + " already there)" : ""));
      } else {
        BV.toast(already === 1 ? "already in the workspace"
                               : "all " + already + " already in the workspace");
      }
      if (opts.jump) BV.openWorkspace();
    });
    return b;
  };

  /* the current backup as a workspace source: {root, label} or null */
  BV.workspace.currentSource = function () {
    var m = BV.state.manifest;
    if (!m || !m.path) return null;
    return { root: m.path, label: m.robot_name || m.name || "" };
  };

  function copy(o) {
    var out = {};
    Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; });
    return out;
  }
  function attrDiff(cur, base) {
    if (!base) return null;
    var out = null;
    ["owner", "comment", "protect"].forEach(function (k) {
      if (cur[k] !== base[k]) (out = out || {})[k] = cur[k];
    });
    return out;
  }
  /* base/current line lists -> body tokens via common prefix/suffix trim */
  function tokens(baseLines, curLines) {
    var p = 0;
    while (p < baseLines.length && p < curLines.length && baseLines[p] === curLines[p]) p++;
    var s = 0;
    while (s < baseLines.length - p && s < curLines.length - p &&
           baseLines[baseLines.length - 1 - s] === curLines[curLines.length - 1 - s]) s++;
    var out = [];
    for (var i = 0; i < p; i++) out.push({ ref: i });
    for (var j = p; j < curLines.length - s; j++) out.push({ text: curLines[j] });
    for (var k = baseLines.length - s; k < baseLines.length; k++) out.push({ ref: k });
    return out;
  }
})();
