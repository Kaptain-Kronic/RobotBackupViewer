/* components/lseditor.js - BV.lsEditor: the single-layer code editor for TP
   program bodies.

   ONE element: a contenteditable <pre> whose content IS the live-highlighted
   text. The earlier transparent-textarea-over-highlighted-pre overlay was two
   renderings of the same text pretending to be one - any style divergence
   (the UA <code> font, bold/italic token weights) made the hidden glyphs
   ghost inside the colored ones. One layer cannot disagree with itself, so
   bold/italic tokens and native selection render correctly by construction.

   What owning the layer costs us (and how it's handled):
   - caret across re-highlights: saved as a text offset (style-independent),
     restored by walking text nodes after every innerHTML rebuild.
   - Enter: intercepted and inserted as a literal "\n" text node ourselves,
     so the DOM stays pure text+spans and behavior is deterministic (and
     testable with synthetic events - our handler does the work, not the
     browser's paragraph machinery). beforeinput catches any path keydown
     misses (numpad enter, insertParagraph from menus).
   - paste: intercepted, plain text only, CRLF normalized to \n.
   - undo/redo: our own snapshot stack (Ctrl+Z/Y/Shift+Z) - innerHTML
     rebuilds would corrupt the browser's native undo, so it's blocked and
     replaced. Consecutive typing coalesces into one undo step.
   - trailing line: "text\n" alone gives the caret no line box to sit on, so
     a sentinel <br data-lsed-end> holds the last empty line open. It is
     invisible to getText() and to caret offsets.

   contenteditable="plaintext-only" is Chromium-native (WebView2 = Chromium):
   typing inserts bare text nodes, never markup. Anything else that sneaks in
   (a stray <br>, dropped rich text) is normalized away by the next repaint -
   getText() treats interior <br> as \n and ignores a trailing one.

   Scrolling: ONE scroller (.lsed-scroll) contains gutter + code side by side;
   vertical scroll moves both natively and position:sticky pins the gutter
   during horizontal scroll - zero sync JS, nothing for a probe window
   (no rAF, no native scroll events) to miss.

   Alignment gaps (setGaps): the pane diff renders "the other side has lines
   here" as NON-EDITABLE spacer blocks between lines - re-emitted from a map
   on every repaint, so they hold vertical space without ever existing in the
   text. getText() skips them, caret offsets never see them, undo snapshots
   stay pure text, and delete-at-a-gap-boundary is intercepted into text
   space (Chromium would otherwise eat the block atomically and the repaint
   would resurrect it - a dead keystroke). lineTop(n) is the gap-aware
   line->pixel map every outside decoration must use instead of n*lineHeight.

   Alt+ArrowUp/Down moves the caret line (or every line the selection
   touches) one line up or down - a text-space edit like the gap-boundary
   delete, so gaps/undo/caret all stay coherent.

   BV.lsEditor(host, {text, onChange})
     -> {el, code, getText, setText, focusLine, setGaps, lineTop} */
(function () {
  "use strict";

  var COALESCE_MS = 800;   /* consecutive typing within this = one undo step */
  var HISTORY_CAP = 200;

  BV.lsEditor = function (host, opts) {
    var root = BV.el("div", { class: "lsed" });
    var scroll = BV.el("div", { class: "lsed-scroll" });
    var gutter = BV.el("div", { class: "lsed-gutter" });
    var nums = BV.el("div", { class: "lsed-nums" });
    gutter.appendChild(nums);
    var code = BV.el("pre", {
      class: "lsed-code",
      spellcheck: "false",
      autocapitalize: "off",
      autocorrect: "off",
      role: "textbox",
      "aria-multiline": "true",
    });
    code.setAttribute("contenteditable", "plaintext-only");
    if (!code.isContentEditable) code.setAttribute("contenteditable", "true");
    scroll.appendChild(gutter);
    scroll.appendChild(code);
    root.appendChild(scroll);
    host.appendChild(root);

    /* ---- text model ------------------------------------------------- */

    function isSentinel(n) {
      return n && n.nodeType === 1 && n.hasAttribute && n.hasAttribute("data-lsed-end");
    }

    /* the DOM as plain text: text nodes verbatim; our sentinel contributes
       NOTHING wherever it sits (it is a line-holder, not content); a browser
       <br> counts as \n when interior (normalization guard - our own DOM
       never keeps one) and as nothing when it is the trailing placeholder
       Chromium leaves in an emptied editor */
    function getText() {
      var parts = [];
      (function walk(n) {
        for (var c = n.firstChild; c; c = c.nextSibling) {
          if (c.nodeType === 3) parts.push(c.nodeValue);
          else if (isSentinel(c)) continue;
          else if (c.nodeName === "BR") parts.push("\n");
          else if (c.nodeType === 1) walk(c);
        }
      })(code);
      var t = parts.join("");
      /* trailing non-sentinel <br>: Chromium's empty-editor placeholder */
      var last = code.lastChild;
      while (last && last.nodeType === 3 && last.nodeValue === "") last = last.previousSibling;
      if (last && last.nodeName === "BR" && !isSentinel(last) && t.endsWith("\n")) {
        t = t.slice(0, -1);
      }
      return t;
    }

    /* caret as {start,end} text offsets - Range.toString() sees the same
       text-node \n's getText() does, and skips <br>s (so does getText's
       trailing-drop; interior <br>s only exist pre-normalization) */
    function caretOffsets() {
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) return null;
      var r = sel.getRangeAt(0);
      if (!code.contains(r.commonAncestorContainer)) return null;
      var probe = document.createRange();
      probe.selectNodeContents(code);
      probe.setEnd(r.startContainer, r.startOffset);
      var start = probe.toString().length;
      probe.setEnd(r.endContainer, r.endOffset);
      return { start: start, end: probe.toString().length };
    }

    function setSelection(start, end) {
      if (end === undefined) end = start;
      var sel = window.getSelection();
      if (!sel) return;
      var range = document.createRange();
      var walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT, null);
      var pos = 0, node, placedStart = false, placedEnd = false;
      while ((node = walker.nextNode())) {
        var len = node.nodeValue.length;
        if (!placedStart && start <= pos + len) {
          range.setStart(node, start - pos);
          placedStart = true;
        }
        if (placedStart && end <= pos + len) {
          range.setEnd(node, end - pos);
          placedEnd = true;
          break;
        }
        pos += len;
      }
      if (!placedStart || !placedEnd) {
        /* past the last text node: the empty trailing line. Sit before the
           sentinel when there is one, else at the very end. */
        var idx = code.childNodes.length;
        if (isSentinel(code.lastChild)) idx -= 1;
        if (!placedStart) range.setStart(code, idx);
        range.setEnd(code, idx);
      }
      try {
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) { /* detached mid-route */ }
    }

    /* ---- painting ---------------------------------------------------- */

    var lastText = null;
    var lastCount = -1;

    /* ---- alignment gaps (the pane-diff's blank rows) --------------------
       gapAfter[n] = blank rows rendered after 1-based line n (0 = before
       line 1). They are NON-EDITABLE spacer blocks re-emitted from this map
       on every repaint, so they hold vertical space without ever existing in
       the text: getText() skips them (no text nodes), caret offsets never
       see them, and undo snapshots stay pure text. Heights are in `lh` so
       they track the text-size setting like every other row.

       Placement rule: a gap block goes AFTER the "\n" separator, BEFORE the
       next line's first token. Chromium suppresses a trailing "\n"'s empty
       line box (the data-lsed-end sentinel exists because of exactly that),
       so "line\n" + block + "next" renders with no phantom rows - whereas
       block-then-"\n" would put a leading newline in the following anonymous
       block and paint a blank line that isn't in the text. */
    var gapAfter = {};

    function gapSpan(n) {
      return '<span class="lsed-gap" data-lsed-gap="1" contenteditable="false" ' +
             'style="height:' + n + 'lh"></span>';
    }

    function setGaps(list) {
      gapAfter = {};
      (list || []).forEach(function (g) {
        if (g && g.n > 0) gapAfter[g.after] = (gapAfter[g.after] || 0) + g.n;
      });
      var t = lastText === null ? getText() : lastText;
      var caret = caretOffsets();
      code.innerHTML = buildHtml(t);
      if (caret) setSelection(caret.start, caret.end);
      lastCount = -1;                    /* the gutter must re-emit its gaps */
      updateGutter(t.split("\n").length);
    }

    /* blank rows above 1-based line n - the bar/scroll math needs real pixels */
    function gapsBefore(n) {
      var s = 0;
      for (var k in gapAfter) if (+k < n) s += gapAfter[k];
      return s;
    }
    function lineTop(n) {
      var cs = getComputedStyle(code);
      var lh = parseFloat(cs.lineHeight) || 16;
      return (parseFloat(cs.paddingTop) || 0) + (n - 1 + gapsBefore(n)) * lh;
    }

    function buildHtml(text) {
      var lines = text.split("\n");
      var html = gapAfter[0] ? gapSpan(gapAfter[0]) : "";
      for (var i = 0; i < lines.length; i++) {
        if (i) {
          html += "\n";
          if (gapAfter[i]) html += gapSpan(gapAfter[i]);
        }
        html += BV.highlightTP(lines[i]);
      }
      if (gapAfter[lines.length]) html += gapSpan(gapAfter[lines.length]);
      /* hold the trailing empty line (or the empty editor) open for the caret */
      if (text === "" || text.endsWith("\n")) html += '<br data-lsed-end="1">';
      return html;
    }

    function updateGutter(count) {
      if (count === lastCount) return;
      lastCount = count;
      var g = gapAfter[0] ? gapSpan(gapAfter[0]) : "";
      for (var n = 1; n <= count; n++) {
        if (n > 1 && gapAfter[n - 1]) g += gapSpan(gapAfter[n - 1]);
        g += n + "\n";
      }
      if (gapAfter[count]) g += gapSpan(gapAfter[count]);
      nums.innerHTML = g;               /* digits + our own spans only */
      nums.style.minWidth = (String(count).length + 0.2) + "ch";
    }

    /* normalize + re-highlight, keeping the caret where the text says it is */
    function repaint() {
      var t = getText();
      var caret = caretOffsets();
      code.innerHTML = buildHtml(t);
      if (caret) setSelection(caret.start, caret.end);
      updateGutter(t.split("\n").length);
      lastText = t;
      return t;
    }

    /* ---- history ----------------------------------------------------- */

    var hist = [], histAt = -1, lastPush = 0, lastWasTyping = false;

    function pushHistory(coalesce) {
      var caret = caretOffsets();
      var entry = {
        t: lastText,
        s: caret ? caret.start : lastText.length,
        e: caret ? caret.end : lastText.length,
      };
      if (histAt >= 0 && hist[histAt].t === entry.t) {
        hist[histAt] = entry;                   /* caret-only change */
        return;
      }
      var now = Date.now();
      if (coalesce && lastWasTyping && histAt >= 0 && now - lastPush < COALESCE_MS) {
        hist[histAt] = entry;                   /* extend the typing burst */
      } else {
        hist.length = ++histAt;                 /* drop any redo tail */
        hist.push(entry);
        if (hist.length > HISTORY_CAP) {
          hist.shift();
          histAt--;
        }
      }
      lastPush = now;
      lastWasTyping = !!coalesce;
    }

    function applyHistory(entry) {
      code.innerHTML = buildHtml(entry.t);
      lastText = entry.t;
      updateGutter(entry.t.split("\n").length);
      setSelection(entry.s, entry.e);
      lastWasTyping = false;
      if (opts && opts.onChange) opts.onChange(entry.t);
    }

    function undo() {
      if (histAt > 0) applyHistory(hist[--histAt]);
    }

    function redo() {
      if (histAt < hist.length - 1) applyHistory(hist[++histAt]);
    }

    /* ---- edits ------------------------------------------------------- */

    function afterEdit(coalesce) {
      var t = repaint();
      pushHistory(coalesce);
      if (opts && opts.onChange) opts.onChange(t);
    }

    /* our own plain-text insertion (Enter, paste): replace the selection
       with a text node - never execCommand, never browser paragraphs */
    function insertPlain(str) {
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) return;
      var r = sel.getRangeAt(0);
      if (!code.contains(r.commonAncestorContainer)) return;
      r.deleteContents();
      var tn = document.createTextNode(str);
      r.insertNode(tn);
      r.setStartAfter(tn);
      r.collapse(true);
      sel.removeAllRanges();
      sel.addRange(r);
      afterEdit(false);
    }

    var composing = false;
    code.addEventListener("compositionstart", function () { composing = true; });
    code.addEventListener("compositionend", function () {
      composing = false;
      afterEdit(false);
    });

    /* Backspace at the start of a line just below a gap (and Delete at the
       end of a line just above one) must eat the NEWLINE. Left to Chromium,
       the non-editable gap block is the caret's neighbor: it deletes the
       block as one atomic unit, the next repaint resurrects it, and the
       keystroke reads as dead. Do the edit in text space instead. */
    function gapBoundaryDelete(key) {
      if (!Object.keys(gapAfter).length) return false;   /* no gaps: all native */
      var caret = caretOffsets();
      if (!caret || caret.start !== caret.end) return false;
      var t = lastText === null ? getText() : lastText;
      var off = caret.start;
      var a, b;
      if (key === "Backspace") {
        if (off === 0 || t[off - 1] !== "\n") return false;
        if (!gapAfter[t.slice(0, off - 1).split("\n").length]) return false;
        a = off - 1; b = off;
      } else {
        if (off >= t.length || t[off] !== "\n") return false;
        if (!gapAfter[t.slice(0, off).split("\n").length]) return false;
        a = off; b = off + 1;
      }
      lastText = t.slice(0, a) + t.slice(b);
      code.innerHTML = buildHtml(lastText);
      setSelection(a, a);
      lastCount = -1;
      updateGutter(lastText.split("\n").length);
      pushHistory(false);
      if (opts && opts.onChange) opts.onChange(lastText);
      return true;
    }

    /* Alt+Arrow: move the caret line - or every line the selection touches -
       one line up/down, the selection riding along (so the gesture repeats).
       Text-space like gapBoundaryDelete: there is no native gesture to lean
       on, and innerHTML rebuilds would fight one anyway. */
    function moveLines(dir) {
      var caret = caretOffsets();
      if (!caret) return false;
      var t = lastText === null ? getText() : lastText;
      var lines = t.split("\n");
      var a = t.slice(0, caret.start).split("\n").length - 1;
      var b = t.slice(0, caret.end).split("\n").length - 1;
      /* a selection ending exactly at a line's column 0 does not claim it */
      if (caret.end > caret.start && t.charAt(caret.end - 1) === "\n") b--;
      var moved, delta;
      if (dir < 0) {
        if (a === 0) return true;              /* nothing above: consumed */
        moved = lines.splice(a - 1, 1)[0];     /* block slides to a-1..b-1 */
        lines.splice(b, 0, moved);             /* ...and this lands after it */
        delta = -(moved.length + 1);
      } else {
        if (b >= lines.length - 1) return true; /* nothing below: consumed */
        moved = lines.splice(b + 1, 1)[0];
        lines.splice(a, 0, moved);
        delta = moved.length + 1;
      }
      lastText = lines.join("\n");
      code.innerHTML = buildHtml(lastText);
      setSelection(caret.start + delta, caret.end + delta);
      lastCount = -1;
      updateGutter(lastText.split("\n").length);
      pushHistory(false);
      if (opts && opts.onChange) opts.onChange(lastText);
      return true;
    }

    code.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        insertPlain("\n");
        return;
      }
      if ((e.key === "Backspace" || e.key === "Delete") &&
          gapBoundaryDelete(e.key)) {
        e.preventDefault();
        return;
      }
      if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey &&
          (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();          /* consumed even at the edges */
        moveLines(e.key === "ArrowUp" ? -1 : 1);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();          /* TP bodies have no tabs; keep focus */
        return;
      }
      var mod = e.ctrlKey || e.metaKey;
      if (mod && !e.altKey && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        if (e.shiftKey) redo(); else undo();
        return;
      }
      if (mod && !e.altKey && !e.shiftKey && (e.key === "y" || e.key === "Y")) {
        e.preventDefault();
        redo();
      }
    });

    code.addEventListener("beforeinput", function (e) {
      /* any newline path keydown didn't see, and the native history that
         would undo our innerHTML rebuilds into garbage */
      if (e.inputType === "insertParagraph" || e.inputType === "insertLineBreak") {
        e.preventDefault();
        insertPlain("\n");
      } else if (e.inputType === "historyUndo") {
        e.preventDefault();
        undo();
      } else if (e.inputType === "historyRedo") {
        e.preventDefault();
        redo();
      }
    });

    code.addEventListener("paste", function (e) {
      e.preventDefault();
      var t = "";
      try { t = (e.clipboardData || window.clipboardData).getData("text/plain") || ""; }
      catch (ex) { t = ""; }
      if (t) insertPlain(t.replace(/\r\n?/g, "\n"));
    });

    /* typing, native deletes, cut, drop - everything else funnels here */
    code.addEventListener("input", function (e) {
      if (composing) return;
      var it = (e && e.inputType) || "";
      afterEdit(it === "insertText" || it === "deleteContentBackward" ||
                it === "deleteContentForward");
    });

    /* ---- public ------------------------------------------------------ */

    function setText(t) {
      code.innerHTML = buildHtml(t || "");
      lastText = t || "";
      lastCount = -1;
      updateGutter(lastText.split("\n").length);
      hist = [];
      histAt = -1;
      lastWasTyping = false;
      pushHistory(false);
    }

    /* 1-based line the caret sits on (text space - gaps invisible), or null
       when the selection is elsewhere. The pane diff pins this line's pixel
       position across a gap re-seat so the text never jumps under the caret. */
    function caretLine() {
      var caret = caretOffsets();
      if (!caret) return null;
      var t = lastText === null ? getText() : lastText;
      return t.slice(0, caret.start).split("\n").length;
    }

    function focusLine(n) {
      var lines = (lastText === null ? getText() : lastText).split("\n");
      var pos = 0;
      for (var i = 0; i < Math.min(n - 1, lines.length); i++) pos += lines[i].length + 1;
      try { code.focus(); } catch (e) { /* probe window */ }
      setSelection(pos, pos);
      /* lineTop, not n*lh: alignment gaps above the line are real pixels */
      scroll.scrollTop = Math.max(0, lineTop(n) - scroll.clientHeight / 2);
    }

    setText((opts && opts.text) || "");

    return {
      el: root,
      code: code,
      getText: getText,
      setText: setText,
      focusLine: focusLine,
      setGaps: setGaps,        /* [{after: 1-based line (0 = top), n: rows}] */
      lineTop: lineTop,        /* 1-based line -> px offset inside the scroller */
      caretLine: caretLine,    /* 1-based caret line (text space), or null */
    };
  };
})();
