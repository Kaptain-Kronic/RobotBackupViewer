/* edit.js - BV.edit: the .ls edit session for the structured editor.

   A buffer per program lives under the per-backup programs tabState bucket
   (survives program<->program navigation, dies with the backup tab). The
   BASELINE is always the PRISTINE backup - exports re-apply every edit to
   pristine bytes server-side, so edits() always diffs against base, while the
   dirty pill compares against the last-exported snapshot (saving doesn't erase
   your edits, it just marks them exported).

   Body edits ride as tokens: {ref:i} = keep pristine record i byte-exact
   (renumbered), {text:s} = canonical new/edited line. The token list is built
   by common prefix/suffix trim between base and current lines, so untouched
   lines keep their exact bytes and a diff of the export shows only what
   actually changed. */
(function () {
  "use strict";

  function store() {
    var pst = BV.tabState("programs");
    return pst._edit || (pst._edit = {});
  }
  function key(file) { return decodeURIComponent(file).toUpperCase(); }

  function attrsDiff(cur, base) {
    var out = null;
    ["owner", "comment", "protect"].forEach(function (k) {
      if (cur[k] !== base[k]) (out = out || {})[k] = cur[k];
    });
    return out;
  }

  /* base/cur line lists -> body tokens via common prefix/suffix trim */
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

  function posKey(e) { return e.id + "|" + e.gp + "|" + e.field; }
  function snapshot(b) {
    return JSON.stringify([b.curBody, b.curAttrs, b.posEdits]);
  }

  BV.edit = {
    /* Promise of the buffer; seeds once per program from pristine bytes */
    ensure: function (file) {
      var s = store(), k = key(file);
      if (s[k]) return Promise.resolve(s[k]);
      return BV.api.call("get_program_editable", decodeURIComponent(file)).then(function (res) {
        var b = {
          file: res.name,
          baseBody: res.body, curBody: res.body,
          baseAttrs: res.attrs, curAttrs: Object.assign({}, res.attrs),
          posEdits: {},
        };
        b.exported = snapshot(b);
        return (s[k] = b);
      });
    },

    peek: function (file) { return store()[key(file)] || null; },

    setBody: function (file, text) {
      var b = store()[key(file)];
      if (b) b.curBody = text;
    },

    setAttr: function (file, name, value) {
      var b = store()[key(file)];
      if (b) b.curAttrs[name] = value;
    },

    /* value === null clears the edit (back to whatever the backup says) */
    setPos: function (file, edit) {
      var b = store()[key(file)];
      if (!b) return;
      if (edit.value === null || edit.value === "") delete b.posEdits[posKey(edit)];
      else b.posEdits[posKey(edit)] = edit;
    },

    getPos: function (file, id, gp, field) {
      var b = store()[key(file)];
      var e = b && b.posEdits[id + "|" + gp + "|" + field];
      return e ? e.value : null;
    },

    dirty: function (file) {
      var b = store()[key(file)];
      return !!b && snapshot(b) !== b.exported;
    },

    dirtyFiles: function () {
      var s = store();
      return Object.keys(s).filter(function (k) {
        return snapshot(s[k]) !== s[k].exported;
      }).map(function (k) { return s[k]; });
    },

    anyDirty: function () { return this.dirtyFiles().length > 0; },

    /* export payload: every buffer that differs from PRISTINE (base), so a
       re-export after more edits carries the full edit set again */
    edits: function () {
      var s = store(), out = [];
      Object.keys(s).forEach(function (k) {
        var b = s[k];
        var body = b.curBody === b.baseBody ? null
          : tokens(b.baseBody.split("\n"), b.curBody.split("\n"));
        var attrs = attrsDiff(b.curAttrs, b.baseAttrs);
        var pos = Object.keys(b.posEdits).map(function (pk) { return b.posEdits[pk]; });
        if (body || attrs || pos.length || snapshot(b) !== b.exported) {
          out.push({ file: b.file, body: body, attrs: attrs,
                     positions: pos.length ? pos : null });
        }
      });
      return out;
    },

    /* after a successful export the current state is the exported state -
       edits stay in the buffer (base stays pristine), the pill goes clean */
    markSaved: function () {
      var s = store();
      Object.keys(s).forEach(function (k) { s[k].exported = snapshot(s[k]); });
    },
  };
})();
