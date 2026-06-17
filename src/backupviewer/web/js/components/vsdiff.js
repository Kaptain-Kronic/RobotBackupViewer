/* vsdiff.js - difference highlighting for "vs" pages.
   One toggle + the same comment/value mode seg the compare report uses;
   each pane tints rows that are missing from, or differ from, the other
   robot under the active mode. */
(function () {
  "use strict";

  /* toolbar controls; calls onChange() whenever state changes.
     returns state = {on, mode} */
  function controls(toolbar, onChange) {
    var state = { on: false, mode: "all" };

    var hl = BV.el("button", {
      class: "btn",
      title: "tint rows that differ from the other robot",
    }, "highlight diffs");
    var seg = BV.el("div", { class: "seg", style: "display:none", title: "what counts as a difference" });
    [["all", "everything"], ["no_comments", "ignore comments"], ["no_values", "ignore values"]]
      .forEach(function (m) {
        var b = BV.el("button", { class: m[0] === state.mode ? "active" : "" }, m[1]);
        b.addEventListener("click", function () {
          if (state.mode === m[0]) return;
          state.mode = m[0];
          seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
          b.classList.add("active");
          onChange();
        });
        seg.appendChild(b);
      });
    hl.addEventListener("click", function () {
      state.on = !state.on;
      hl.classList.toggle("primary", state.on);
      seg.style.display = state.on ? "" : "none";
      onChange();
    });
    toolbar.appendChild(hl);
    toolbar.appendChild(seg);
    return state;
  }

  /* pane rowClass factory: rows missing from the other side, or differing
     under the mode, get .vsdiff */
  function marker(state, otherRows, keyFn, differs) {
    var map = {};
    (otherRows || []).forEach(function (r) { map[keyFn(r)] = r; });
    return function (row) {
      if (!state.on) return "";
      var other = map[keyFn(row)];
      if (!other) return "vsdiff";
      return differs(row, other, state.mode) ? "vsdiff" : "";
    };
  }

  /* shared field comparators */
  function ioDiffers(a, b, mode) {
    var cmt = a.comment !== b.comment;
    var asg = (a.rack + ":" + a.slot + ":" + a.port) !== (b.rack + ":" + b.slot + ":" + b.port);
    if (mode === "no_comments") return asg;
    if (mode === "no_values") return cmt;
    return cmt || asg;
  }

  function regDiffers(a, b, mode) {
    var cmt = (a.comment || "") !== (b.comment || "");
    var val;
    if (a.kind || b.kind) { /* position registers */
      val = JSON.stringify([a.kind, a.joints, a.x, a.y, a.z, a.w, a.p, a.r]) !==
        JSON.stringify([b.kind, b.joints, b.x, b.y, b.z, b.w, b.p, b.r]);
    } else {
      val = String(a.value) !== String(b.value);
    }
    if (mode === "no_comments") return val;
    if (mode === "no_values") return cmt;
    return cmt || val;
  }

  function progDiffers(a, b, mode) {
    var cmt = (a.comment || "") !== (b.comment || "");
    var val = (a.modified !== b.modified) || (a.prog_size !== b.prog_size) ||
      (a.line_count !== b.line_count);
    if (mode === "no_comments") return val;
    if (mode === "no_values") return cmt;
    return cmt || val;
  }

  function macroDiffers(a, b) {
    return a.prog_name !== b.prog_name || a.assign_type !== b.assign_type ||
      a.assign_id !== b.assign_id;
  }

  BV.vsDiff = {
    controls: controls,
    marker: marker,
    io: ioDiffers,
    reg: regDiffers,
    prog: progDiffers,
    macro: macroDiffers,
  };
})();
