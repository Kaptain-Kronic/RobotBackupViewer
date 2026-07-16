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
    function close(force) {
      if (force !== true && opts && opts.beforeClose && !opts.beforeClose()) return;
      root.classList.add("hidden");
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

  /* small anchored context menu (right-click-style popup). items is a list of
     {label, onClick, danger?}. The menu floats just under anchorEl and dismisses
     itself on outside-click, Esc, scroll, or resize. Returns {close}. */
  BV.menu = function (anchorEl, items) {
    var menu = BV.el("div", { class: "ctx-menu" });
    items.forEach(function (it) {
      /* it.action = {label,title,onClick}: a small trailing pill on the row with
         its own click (e.g. the date-picker's "vs" -> compare with that date) */
      var b = BV.el("button", { class: "ctx-item" + (it.danger ? " danger" : "") },
        BV.esc(it.label) +
        (it.action ? '<span class="ctx-act" title="' + BV.esc(it.action.title || "") + '">' +
          BV.esc(it.action.label) + "</span>" : ""));
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        close();
        if (it.onClick) it.onClick();
      });
      var act = it.action && b.querySelector(".ctx-act");
      if (act) {
        act.addEventListener("click", function (e) {
          e.stopPropagation();
          close();
          it.action.onClick();
        });
      }
      menu.appendChild(b);
    });
    /* Append to <html> so no ancestor's overflow can clip it. (The page-zoom
       era needed a transform compensation here; the zoom is retired with the
       v0.98 chrome/content scale split, so fixed coords just work.) */
    document.documentElement.appendChild(menu);

    var r = anchorEl.getBoundingClientRect();
    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    var left = r.left;
    if (left + mw > window.innerWidth - 8) left = Math.max(8, r.right - mw);  /* spill leftward */
    var top = r.bottom + 4;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - 4 - mh); /* flip above */
    menu.style.left = left + "px";
    menu.style.top = top + "px";

    function close() {
      if (!menu.parentNode) return;
      menu.parentNode.removeChild(menu);
      document.removeEventListener("mousedown", onOutside, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    }
    function onOutside(e) { if (!menu.contains(e.target)) close(); }
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
    /* page scroll closes the menu, but scrolling INSIDE it (long lists) must not */
    function onScroll(e) { if (!menu.contains(e.target)) close(); }
    /* defer the listeners so the click that opened the menu doesn't close it */
    setTimeout(function () {
      document.addEventListener("mousedown", onOutside, true);
      document.addEventListener("keydown", onKey, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", close);
    }, 0);
    return { close: close };
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

  BV.collapseAll = function (root, expand) {
    var nodes = [];
    if (root.classList && root.classList.contains("bv-collapsible")) nodes.push(root);
    root.querySelectorAll(".bv-collapsible").forEach(function (n) { nodes.push(n); });
    nodes.forEach(function (n) {
      BV.setOpen(n, expand);
      if (n._bvOnToggle) n._bvOnToggle(expand);
    });
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
