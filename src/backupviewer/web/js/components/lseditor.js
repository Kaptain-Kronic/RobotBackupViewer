/* components/lseditor.js - BV.lsEditor: the overlay code editor for TP bodies.
   A transparent textarea sits exactly over a live-highlighted <pre>; a gutter
   auto-numbers the lines. The user types plain instruction text - no line
   numbers, no trailing ';' (both are serialization details the exporter
   re-applies). Alignment was proven in a standalone sandbox: identical font
   metrics on both layers + scroll sync, no rAF anywhere (probe windows have
   none), long lines scroll sideways (wrap=off) pendant-style.

   BV.lsEditor(host, {text, onChange}) -> {el, ta, setText, focusLine} */
(function () {
  "use strict";

  BV.lsEditor = function (host, opts) {
    var root = BV.el("div", { class: "lsed" });
    var gutter = BV.el("div", { class: "lsed-gutter" });
    var nums = BV.el("div", { class: "lsed-nums" });
    gutter.appendChild(nums);
    var body = BV.el("div", { class: "lsed-body" });
    /* the highlight layer is a bare <pre> - NOT <pre><code>. A <code> child
       picks up the UA rule `code { font-family: monospace }` (Courier), a
       DIFFERENT font from the textarea's inherited mono, so the two layers
       render at different glyph widths and never line up. The <pre> itself
       inherits our font via the .lsed-hl rule. */
    var hl = BV.el("pre", { class: "lsed-hl" });
    var ta = document.createElement("textarea");
    ta.className = "lsed-ta";
    ta.spellcheck = false;
    ta.wrap = "off";
    ta.setAttribute("autocapitalize", "off");
    ta.setAttribute("autocomplete", "off");
    ta.setAttribute("autocorrect", "off");
    body.appendChild(hl);
    body.appendChild(ta);
    root.appendChild(gutter);
    root.appendChild(body);
    host.appendChild(root);

    var lastCount = -1;
    function paint() {
      var lines = ta.value.split("\n");
      var html = "";
      for (var i = 0; i < lines.length; i++) html += BV.highlightTP(lines[i]) + "\n";
      hl.innerHTML = html;
      if (lines.length !== lastCount) {
        lastCount = lines.length;
        var g = "";
        for (var n = 1; n <= lines.length; n++) g += n + "\n";
        nums.textContent = g;
        nums.style.minWidth = (String(lines.length).length + 0.2) + "ch";
      }
    }
    function sync() {
      hl.scrollTop = ta.scrollTop;
      hl.scrollLeft = ta.scrollLeft;
      nums.style.transform = "translateY(" + (-ta.scrollTop) + "px)";
    }
    ta.addEventListener("input", function () {
      paint();
      if (opts && opts.onChange) opts.onChange(ta.value);
    });
    ta.addEventListener("scroll", sync);

    ta.value = (opts && opts.text) || "";
    paint();

    return {
      el: root,
      ta: ta,
      setText: function (t) { ta.value = t || ""; paint(); sync(); },
      /* put the caret on line n (1-based) and scroll it into view - the edit
         twin of the view mode's revealLine */
      focusLine: function (n) {
        var lines = ta.value.split("\n");
        var pos = 0;
        for (var i = 0; i < Math.min(n - 1, lines.length); i++) pos += lines[i].length + 1;
        try { ta.focus(); ta.setSelectionRange(pos, pos); } catch (e) { /* probe window */ }
        var lh = parseFloat(getComputedStyle(ta).lineHeight) || 16;
        ta.scrollTop = Math.max(0, (n - 1) * lh - ta.clientHeight / 2);
        sync();
      },
    };
  };
})();
