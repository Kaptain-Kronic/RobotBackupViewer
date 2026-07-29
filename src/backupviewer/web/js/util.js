/* util.js - BV namespace + dom helpers. Loaded first; everything attaches to window.BV. */
window.BV = {};

(function () {
  "use strict";

  /* Compound-key separator: the NUL character — it can never appear in robot
     names, program names, or file paths. Built at RUNTIME on purpose: a raw
     NUL byte in source makes git/grep treat the whole file as binary, and
     spelling it as a backslash-u escape in source has repeatedly been decoded
     into a raw NUL by editing tools (that exact bug has shipped three times).
     Always use BV.KEYSEP; never inline either form. api.js (in-flight request
     keys) and compare.js (hide keys) build their compound keys from this. */
  BV.KEYSEP = String.fromCharCode(0);

  BV.esc = function (s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  BV.el = function (tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") e.className = attrs[k];
        else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), attrs[k]);
        else e.setAttribute(k, attrs[k]);
      });
    }
    if (html !== undefined) e.innerHTML = html;
    return e;
  };

  BV.debounce = function (fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  };

  BV.fmt = {
    num: function (v, digits) {
      if (v === null || v === undefined) return "—";
      return Number(v).toFixed(digits === undefined ? 3 : digits);
    },
    bytes: function (n) {
      if (n === null || n === undefined) return "—";
      if (n < 1024) return n + " B";
      if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
      return (n / 1048576).toFixed(2) + " MB";
    },
    kb: function (kb) {
      if (kb >= 1024) return (kb / 1024).toFixed(1) + " MB";
      return kb.toFixed(1) + " KB";
    },
    date: function (iso) {
      if (!iso) return "—";
      return String(iso).replace("T", " ");
    },
    epoch: function (sec) {
      if (!sec) return "—";
      var d = new Date(sec * 1000);
      var p = function (x) { return String(x).padStart(2, "0"); };
      return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
        " " + p(d.getHours()) + ":" + p(d.getMinutes());
    },
  };

  var toastEl = null, toastTimer = null;
  BV.toast = function (msg, ms) {
    if (!toastEl) {
      toastEl = BV.el("div", { id: "toast" });
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, ms || 1800);
  };

  /* Fullscreen, the only way that works here - one path for every caller.

     NOT the web Fullscreen API: WebView2 grants requestFullscreen (no error,
     fullscreenElement gets set) but only stretches the element inside the same
     window, and our overlays are already inset:0 - so the button appeared to do
     nothing at all. Fullscreen belongs to the HOST window (pywebview
     toggle_fullscreen), which Python owns, so Python owns the state too; this
     window asks about itself via BV.windowKey(). */
  BV.fullscreen = (function () {
    var on = false;
    var busy = null;   /* the in-flight toggle - a double esc/click must not
                          fire twice and land the window back where it started */
    function toggle() {
      if (busy) return busy;
      busy = BV.api.call("toggle_fullscreen", { window: BV.windowKey() })
        .then(function (r) { on = !!(r && r.fullscreen); return on; })
        .catch(function (e) { BV.toast("fullscreen: " + e.message); return on; })
        .then(function (v) { busy = null; return v; });
      return busy;
    }
    return {
      active: function () { return on; },
      toggle: toggle,
      exit: function () { return on ? toggle() : Promise.resolve(false); },
    };
  })();

  /* clipboard with the WebView2-safe fallback; every report/copy button in the
     app (scan report, backup log, future exports) shares this one path */
  BV.copyText = function (text, okMsg) {
    function done() { BV.toast(okMsg || "copied"); }
    function fallback() {
      var ta = BV.el("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); }
      catch (e) { BV.toast("copy failed"); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else fallback();
  };

  /* simple modal helper; returns {close}. opts.beforeClose() -> false blocks a
     dismissal (backdrop / Esc / cancel) — the unsaved-work guard. close(true)
     bypasses it for a committed save or an explicit discard; the check is
     strictly === true so event objects passed by listeners can't bypass. */
  BV.modal = function (title, bodyEl, opts) {
    var root = document.getElementById("modal-root");
    root.innerHTML = "";
    var m = BV.el("div", { class: "modal" });
    if (title) m.appendChild(BV.el("h2", null, BV.esc(title)));
    var body = BV.el("div", { class: "modal-body" });
    body.appendChild(bodyEl);
    m.appendChild(body);
    root.appendChild(m);
    root.classList.remove("hidden");
    /* opts.seeThrough drops the scrim and its blur for THIS dialog: a panel
       whose whole job is tuning the background cannot also be hiding it. */
    root.classList.toggle("see-through", !!(opts && opts.seeThrough));
    function close(force) {
      if (force !== true && opts && opts.beforeClose && !opts.beforeClose()) return;
      root.classList.add("hidden");
      root.classList.remove("see-through");
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey, true);
      root.removeEventListener("mousedown", onBackdrop);
      if (opts && opts.onClose) opts.onClose();
    }
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(); }
      else if (opts && opts.onKey && opts.onKey(e, close)) { e.stopPropagation(); e.preventDefault(); }
    }
    function onBackdrop(e) { if (e.target === root) close(); }
    document.addEventListener("keydown", onKey, true);
    root.addEventListener("mousedown", onBackdrop);
    return { close: close, el: m };
  };

  BV.modalOpen = function () {
    return !document.getElementById("modal-root").classList.contains("hidden");
  };

  /* opt-in unsaved-work guard for BV.modal (pass as opts.beforeClose): while
     dirty() is true, a dismissal first warns, and only a SECOND attempt within
     4s discards — a single stray click outside the window can never eat work.
     (100% of theme-editor testers lost near-finished themes to exactly that.) */
  BV.dirtyGuard = function (dirty, what) {
    var armedAt = 0;
    return function () {
      if (!dirty()) return true;
      var now = Date.now();
      if (now - armedAt < 4000) return true;
      armedAt = now;
      BV.toast("unsaved " + (what || "changes") + " — press again to discard", 2600);
      return false;
    };
  };

  /* ---- anchored floating surfaces ----
     One placement + dismissal contract, shared by BV.menu (a list of items) and
     BV.dropPanel (caller-owned content, e.g. the theme picker). Both append to
     <html> so no ancestor's overflow can clip them, and both dismiss on
     outside-click, Esc, scroll, or resize.

     Clicking the anchor while its own surface is open TOGGLES it closed: the
     outside-mousedown closes it, and the click that follows would land on the
     anchor and instantly reopen it — that call is swallowed. */
  var _justClosed = { el: null, at: 0 };
  function swallowReopen(anchorNode) {
    if (anchorNode && _justClosed.el === anchorNode && Date.now() - _justClosed.at < 500) {
      _justClosed.el = null;
      return true;
    }
    return false;
  }

  /* fixed coords under the anchor (an ELEMENT) or at a POINT {x, y} for
     right-click menus; spills leftward and flips above when the viewport runs
     out. (The page-zoom era needed a transform compensation here; the zoom is
     retired with the v0.98 chrome/content scale split, so fixed coords work.) */
  function placeFloat(el, anchorEl, alignRight) {
    var anchorNode = anchorEl && anchorEl.nodeType === 1 ? anchorEl : null;
    var r = anchorNode ? anchorNode.getBoundingClientRect()
      : { left: anchorEl.x, right: anchorEl.x, top: anchorEl.y - 4, bottom: anchorEl.y - 4 };
    var mw = el.offsetWidth, mh = el.offsetHeight;
    /* right-aligned surfaces hang off the anchor's right edge, so a value
       control stays flush with the column of controls above it */
    var left = alignRight ? Math.max(8, r.right - mw) : r.left;
    if (left + mw > window.innerWidth - 8) left = Math.max(8, r.right - mw);  /* spill leftward */
    var top = r.bottom + 4;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - 4 - mh); /* flip above */
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  /* close() must stay safe against ANY ordering: the dismiss listeners attach
     DEFERRED (below), so a surface closed in its opening tick (an item clicked
     immediately) must both skip the attach and not try to remove listeners that
     never existed. The old "already detached -> return" guard silently kept
     deferred listeners alive forever — a leaked document-CAPTURE Escape handler
     that swallowed the key for every element underneath it from then on.

     opts: {anchorNode, escOnWindow, onKey(e)->bool, onClose}. */
  function wireDismiss(el, opts) {
    var keyTarget = opts.escOnWindow ? window : document;
    var closed = false, attached = false;
    function close() {
      if (closed) return;
      closed = true;
      if (el.parentNode) el.parentNode.removeChild(el);
      if (attached) {
        document.removeEventListener("mousedown", onOutside, true);
        keyTarget.removeEventListener("keydown", onKey, true);
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", close);
      }
      if (opts.onClose) opts.onClose();
    }
    function onOutside(e) {
      if (el.contains(e.target)) return;
      /* closing because the anchor itself was pressed: remember it so the
         click that follows toggles instead of reopening */
      if (opts.anchorNode && opts.anchorNode.contains(e.target)) {
        _justClosed = { el: opts.anchorNode, at: Date.now() };
      }
      close();
    }
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if (opts.onKey && opts.onKey(e)) { e.stopPropagation(); e.preventDefault(); }
    }
    /* page scroll closes it, but scrolling INSIDE it (long lists) must not */
    function onScroll(e) { if (!el.contains(e.target)) close(); }
    /* defer the listeners so the click that opened it doesn't close it */
    setTimeout(function () {
      if (closed) return;                    /* closed before we ever attached */
      attached = true;
      document.addEventListener("mousedown", onOutside, true);
      keyTarget.addEventListener("keydown", onKey, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", close);
    }, 0);
    return { close: close };
  }

  /* small anchored context menu (right-click-style popup). items is a list of
     {label, onClick, danger?}. Returns {close}.

     Esc is captured on WINDOW (see wireDismiss): a menu opened from inside a
     dialog — the ⚙ effect picker — used to lose the key to the dialog's own
     document-capture handler, which was registered first, so Esc closed the
     whole dialog instead of just the menu. Outside a dialog it means Esc
     dismisses the menu WITHOUT also backing out of the view underneath. */
  BV.menu = function (anchorEl, items) {
    var anchorNode = anchorEl && anchorEl.nodeType === 1 ? anchorEl : null;
    if (swallowReopen(anchorNode)) return { close: function () {} };
    var handle = null;
    var menu = BV.el("div", { class: "ctx-menu" });
    items.forEach(function (it) {
      /* it.action = {label,title,onClick}: a small trailing pill on the row with
         its own click (e.g. the date-picker's "vs" -> compare with that date) */
      var b = BV.el("button", { class: "ctx-item" + (it.danger ? " danger" : "") },
        BV.esc(it.label) +
        (it.action ? '<span class="ctx-act" title="' + BV.esc(it.action.title || "") + '">' +
          BV.esc(it.action.label) + "</span>" : ""));
      if (it.disabled) b.disabled = true;   /* e.g. selection-gated rows */
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        if (handle) handle.close();
        if (it.onClick) it.onClick();
      });
      var act = it.action && b.querySelector(".ctx-act");
      if (act) {
        act.addEventListener("click", function (e) {
          e.stopPropagation();
          if (handle) handle.close();
          it.action.onClick();
        });
      }
      menu.appendChild(b);
    });
    document.documentElement.appendChild(menu);
    placeFloat(menu, anchorEl, false);
    handle = wireDismiss(menu, { anchorNode: anchorNode, escOnWindow: true });
    return handle;
  };

  /* An anchored panel with caller-owned content — same placement and dismissal
     as BV.menu, but you fill it (a filter + a list, a mini tree). Returns
     {close}, or null when this call is the swallowed half of a toggle.

     Esc is captured on WINDOW, not document: a panel opened from inside a
     dialog must not let the dialog's own Esc handler (a document-capture
     listener registered first, so it would win) close the dialog underneath.
     opts: {align: "right", onKey(e)->bool, onClose}. */
  BV.dropPanel = function (anchorEl, contentEl, opts) {
    opts = opts || {};
    var anchorNode = anchorEl && anchorEl.nodeType === 1 ? anchorEl : null;
    if (swallowReopen(anchorNode)) return null;
    var panel = BV.el("div", { class: "bv-drop" });
    var body = BV.el("div", { class: "bv-drop-body" });
    body.appendChild(contentEl);
    panel.appendChild(body);
    document.documentElement.appendChild(panel);
    placeFloat(panel, anchorEl, opts.align === "right");
    return wireDismiss(panel, {
      anchorNode: anchorNode,
      escOnWindow: true,
      onKey: opts.onKey,
      onClose: opts.onClose,
    });
  };

  /* ---- collapsible primitive ----
     Standard collapsible node used by trees (sysvars, DCS entries, call tree).
     Structure: a container with class bv-collapsible (+.open when expanded),
     a head element containing a .bv-caret, and a body element.
     Left-click toggles this node; right-click expands/collapses the whole
     subtree under it. opts: {open, onToggle(open)}. */
  BV.setOpen = function (node, open) {
    node.classList.toggle("open", open);
    var caret = node.querySelector(":scope > * .bv-caret, :scope > .bv-caret");
    if (caret) caret.textContent = open ? "▾" : "▸";
  };

  /* right-click on a head: expand/collapse everything UNDER the node. The
     clicked node itself never collapses as a side effect — you right-clicked
     to fold the children, not the folder you're holding — but expanding DOES
     open a closed node, so the result is never invisible. Leaf nodes (no
     collapsible children) are a no-op: left-click is the toggle. */
  BV.subtreeToggle = function (node) {
    var kids = Array.prototype.slice.call(node.querySelectorAll(".bv-collapsible"));
    if (!kids.length) return;
    var expand = !kids.every(function (n) { return n.classList.contains("open"); });
    kids.forEach(function (n) {
      BV.setOpen(n, expand);
      if (n._bvOnToggle) n._bvOnToggle(expand);
    });
    if (expand && !node.classList.contains("open")) {
      BV.setOpen(node, true);
      if (node._bvOnToggle) node._bvOnToggle(true);
    }
  };

  BV.collapsible = function (node, head, body, opts) {
    opts = opts || {};
    node.classList.add("bv-collapsible");
    head.classList.add("bv-collapse-head");
    if (body) body.classList.add("bv-collapse-body");
    if (!head.querySelector(".bv-caret")) {
      head.insertBefore(BV.el("span", { class: "bv-caret" }, "▸"), head.firstChild);
    }
    if (opts.onToggle) node._bvOnToggle = opts.onToggle;
    /* no onToggle echo here: construction just PAINTS opts.open. Notifying it
       let a forced-open render (library filter) overwrite remembered fold
       state — onToggle now means "the user toggled it", nothing else. */
    BV.setOpen(node, !!opts.open);
    head.addEventListener("click", function () {
      var open = !node.classList.contains("open");
      BV.setOpen(node, open);
      if (opts.onToggle) opts.onToggle(open);
    });
    head.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      BV.subtreeToggle(node);
    });
    return node;
  };
})();
