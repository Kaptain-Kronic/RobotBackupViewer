/* workspace.js - BV.workspace: the multi-robot edit working set.

   State is MODULE-LEVEL on purpose. BV.tabState is per-backup (state.js
   re-points tabData on every setManifest and dropBucket deletes it when a
   backup tab closes), so a working set kept there would vanish the moment you
   switched robots - and would read empty with no backup open at all, which is
   exactly when the workspace still has to work.

   Every entry has a STABLE id. It is not derived from the rename, so renaming
   a program keeps its open tabs and its undo history; and two entries may share
   one source file with different names, which is what a duplicate IS. The id
   embeds root + relative path (never the basename - every robot has a MAIN.LS)
   plus a per-source sequence number.

   Programs are read and exported through the path-addressed ws_* endpoints, so
   the workspace never opens a session and is not bound by MAX_OPEN_SESSIONS.

   The working set and any unsaved drafts persist to %APPDATA% via set_setting
   (never localStorage), so closing the app does not lose your edits. */
(function () {
  "use strict";

  var MAX_DRAFT_BYTES = 4 * 1024 * 1024;   /* keep settings.json sane */

  var entries = [];    /* [{id, root, file, name, label, saveAs}] insertion order */
  var bufs = {};       /* id -> {text, base, attrs, baseAttrs, positions, posEdits, exported} */
  var loaded = false;

  function byId(id) {
    for (var i = 0; i < entries.length; i++) if (entries[i].id === id) return entries[i];
    return null;
  }
  function idFor(root, file, seq) {
    return root + BV.KEYSEP + file + BV.KEYSEP + seq;
  }
  function nextSeq(root, file) {
    var n = 0;
    entries.forEach(function (e) {
      if (e.root === root && e.file === file) n++;
    });
    return n;
  }
  function changed() {
    BV.state.emit("workspace", entries.length);
    save();
  }

  /* ---- persistence ---- */
  var saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var drafts = {}, bytes = 0, dropped = 0;
      Object.keys(bufs).forEach(function (id) {
        var b = bufs[id];
        if (!isDirtyBuf(b)) return;
        var blob = JSON.stringify({ t: b.text, a: b.attrs, p: b.posEdits });
        if (bytes + blob.length > MAX_DRAFT_BYTES) { dropped++; return; }
        bytes += blob.length;
        drafts[id] = { t: b.text, a: b.attrs, p: b.posEdits };
      });
      if (dropped) BV.toast(dropped + " draft(s) too large to remember", 3000);
      BV.api.call("set_setting", "ws_entries", entries).catch(function () {});
      BV.api.call("set_setting", "ws_drafts", drafts).catch(function () {});
    }, 400);
  }

  function isDirtyBuf(b) {
    if (!b) return false;
    /* exported === null means never stamped - a draft restored from settings,
       which is unsaved work by definition */
    if (b.exported === null) return true;
    return JSON.stringify([b.text, b.attrs, b.posEdits]) !== b.exported;
  }
  function stamp(b) { b.exported = JSON.stringify([b.text, b.attrs, b.posEdits]); }

  BV.workspace = {
    load: function () {
      if (loaded) return;
      loaded = true;
      var s = BV.state.settings || {};
      var saved = s.ws_entries, drafts = s.ws_drafts || {};
      if (Object.prototype.toString.call(saved) === "[object Array]") {
        saved.forEach(function (e) {
          if (!e || !e.root || !e.file) return;
          entries.push({
            id: e.id || idFor(e.root, e.file, nextSeq(e.root, e.file)),
            root: e.root, file: e.file,
            name: e.name || e.file.split("/").pop(),
            label: e.label || "", saveAs: e.saveAs || "",
          });
        });
      }
      Object.keys(drafts).forEach(function (id) {
        var d = drafts[id];
        if (!d) return;
        /* keep the text; base/baseAttrs fill in on the first buffer() call */
        bufs[id] = { text: d.t, base: null, attrs: d.a || {}, baseAttrs: null,
                     positions: [], posEdits: d.p || {}, exported: null };
      });
      if (entries.length) BV.state.emit("workspace", entries.length);
    },

    entries: function () { return entries.slice(); },
    count: function () { return entries.length; },
    byId: byId,
    /* what the tabs and rail show: the rename if there is one */
    displayName: function (e) {
      if (!e) return "";
      return e.saveAs ? e.saveAs + ".LS" : e.name;
    },
    /* is this SOURCE already in the set (ignoring duplicates' names)? */
    hasSource: function (root, file) {
      return entries.some(function (e) { return e.root === root && e.file === file; });
    },

    /* add: {root, file, name?, label?}. A source already present is not added
       again - use duplicate() for a second copy under a new name. */
    add: function (e) {
      if (!e || !e.root || !e.file) return null;
      if (this.hasSource(e.root, e.file)) return null;
      var entry = {
        id: idFor(e.root, e.file, nextSeq(e.root, e.file)),
        root: e.root, file: e.file,
        name: e.name || e.file.split("/").pop(),
        label: e.label || "", saveAs: "",
      };
      entries.push(entry);
      changed();
      return entry;
    },
    addMany: function (list) {
      var n = 0;
      (list || []).forEach(function (e) { if (BV.workspace.add(e)) n++; });
      return n;
    },

    /* a second entry on the same source under a new name, with its OWN buffer.
       emptyBody starts it blank - the cheapest honest "new program", since the
       header it clones is one a real controller already accepted. */
    duplicate: function (id, newName, emptyBody) {
      var src = byId(id);
      if (!src) return null;
      var base = String(newName || "").replace(/\.[Ll][Ss]$/, "");
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(base)) return null;
      if (this.nameTaken(src.root, base, null)) return null;
      var entry = {
        id: idFor(src.root, src.file, nextSeq(src.root, src.file)),
        root: src.root, file: src.file, name: src.name,
        label: src.label, saveAs: base,
      };
      entries.push(entry);
      var from = bufs[id];
      if (from && from.base !== null) {
        bufs[entry.id] = {
          text: emptyBody ? "" : from.text, base: from.base,
          attrs: copy(from.attrs), baseAttrs: from.baseAttrs,
          positions: from.positions || [], posEdits: {}, exported: null,
        };
      } else if (emptyBody) {
        /* seed lazily but remember it must come up empty */
        bufs[entry.id] = { text: "", base: null, attrs: {}, baseAttrs: null,
                           positions: [], posEdits: {}, exported: null };
      }
      changed();
      return entry;
    },

    /* is a program name already used for this robot in the workspace? */
    nameTaken: function (root, base, exceptId) {
      var want = String(base || "").toUpperCase();
      return entries.some(function (e) {
        if (e.root !== root || e.id === exceptId) return false;
        var n = (e.saveAs ? e.saveAs : e.name.replace(/\.[Ll][Ss]$/, "")).toUpperCase();
        return n === want;
      });
    },

    /* "" clears the rename (export under the source's own name) */
    rename: function (id, newName) {
      var e = byId(id);
      if (!e) return false;
      var base = String(newName || "").replace(/\.[Ll][Ss]$/, "");
      if (base && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(base)) return false;
      if (base && this.nameTaken(e.root, base, id)) return false;
      /* clearing a rename must not collide with the source name either */
      e.saveAs = base;
      changed();
      return true;
    },

    remove: function (id) {
      var i = -1;
      entries.forEach(function (e, k) { if (e.id === id) i = k; });
      if (i < 0) return false;
      entries.splice(i, 1);
      delete bufs[id];
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
          g = groups[e.root] = { root: e.root,
                                 label: e.label || e.root.split(/[\\/]/).pop(),
                                 programs: [] };
          order.push(g);
        }
        g.programs.push(e);
      });
      return order;
    },

    /* promise of the buffer for an ENTRY, seeded from PRISTINE bytes on first use */
    buffer: function (e) {
      if (!e) return Promise.reject(new Error("no entry"));
      var b = bufs[e.id];
      if (b && b.base !== null) return Promise.resolve(b);
      return BV.api.call("ws_get_program", e.root, e.file).then(function (res) {
        var cur = bufs[e.id];
        if (cur) {
          cur.base = res.body;
          cur.baseAttrs = res.attrs;
          cur.positions = res.positions || [];
          if (!cur.attrs || !Object.keys(cur.attrs).length) cur.attrs = copy(res.attrs);
          return cur;
        }
        b = bufs[e.id] = {
          text: res.body, base: res.body,
          attrs: copy(res.attrs), baseAttrs: res.attrs,
          positions: res.positions || [], posEdits: {}, exported: null,
        };
        stamp(b);
        return b;
      });
    },
    peek: function (e) { return (e && bufs[e.id]) || null; },

    setBody: function (e, text) {
      var b = e && bufs[e.id];
      if (!b) return;
      b.text = text;
      changed();
    },
    setAttr: function (e, name, value) {
      var b = e && bufs[e.id];
      if (!b) return;
      b.attrs[name] = value;
      changed();
    },
    setPos: function (e, edit) {
      var b = e && bufs[e.id];
      if (!b) return;
      var pk = edit.id + "|" + edit.gp + "|" + edit.field;
      if (edit.value === null || edit.value === "") delete b.posEdits[pk];
      else b.posEdits[pk] = edit;
      changed();
    },
    getPos: function (e, id, gp, field) {
      var b = e && bufs[e.id];
      var x = b && b.posEdits[id + "|" + gp + "|" + field];
      return x ? x.value : null;
    },

    dirty: function (e) { return isDirtyBuf(e && bufs[e.id]); },
    dirtyCount: function () {
      return Object.keys(bufs).filter(function (id) { return isDirtyBuf(bufs[id]); }).length;
    },
    anyDirty: function () { return this.dirtyCount() > 0; },

    /* ws_export payload: every entry whose buffer differs from PRISTINE, plus
       every entry carrying a rename (a rename IS a change even with no edits) */
    edits: function () {
      var out = [];
      entries.forEach(function (e) {
        var b = bufs[e.id];
        if (!b || b.base === null) {
          /* never opened: only a rename is worth exporting */
          if (e.saveAs) {
            out.push({ root: e.root, file: e.file, label: e.label,
                       save_as: e.saveAs, body: null, attrs: null, positions: null });
          }
          return;
        }
        var body = b.text === b.base ? null : tokens(b.base.split("\n"), b.text.split("\n"));
        var attrs = attrDiff(b.attrs, b.baseAttrs);
        var pos = Object.keys(b.posEdits).map(function (k) { return b.posEdits[k]; });
        if (!body && !attrs && !pos.length && !e.saveAs) return;
        out.push({ root: e.root, file: e.file, label: e.label,
                   save_as: e.saveAs || "", body: body, attrs: attrs,
                   positions: pos.length ? pos : null });
      });
      return out;
    },

    markSaved: function () {
      Object.keys(bufs).forEach(function (id) { stamp(bufs[id]); });
      changed();
    },
  };

  /* ---- shared affordance ----
     One button used by the programs list, the program detail view and both
     sides of a compare, so "add to workspace" behaves identically everywhere. */
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

  /* the current backup as a workspace source: {root, label} or null.
     root_key, not path: `path` is the session id and carries whatever spelling
     opened it (8.3 short name, mapped drive), so keying on it would file the
     same backup under two roots when it is also reached from the library. */
  BV.workspace.currentSource = function () {
    var m = BV.state.manifest;
    if (!m || !(m.root_key || m.path)) return null;
    return { root: m.root_key || m.path, label: m.robot_name || m.name || "" };
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
  /* base/current line lists -> body tokens via common prefix/suffix trim, so
     untouched lines stay byte-exact in the export */
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
