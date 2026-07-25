/* keys.js - global keyboard map. Tab-local list navigation goes through BV.currentVTable. */
(function () {
  "use strict";

  function typing() {
    var a = document.activeElement;
    return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);
  }

  /* a tab re-render can leave BV.currentVTable/currentSearch pointing at a
     detached instance from the previous render - only act on live ones */
  function liveVT() {
    var vt = BV.currentVTable;
    return vt && document.contains(vt.container) ? vt : null;
  }
  function liveSearch() {
    var s = BV.currentSearch;
    return s && document.contains(s.el) ? s : null;
  }

  function helpOverlay() {
    var body = BV.el("div");
    var rows = [
      ["1 – 9 · 0 · - · =", "switch tab (the number row; 0 = 3d view)"],
      ["ctrl+k", "search whole backup"],
      ["backspace", "back (previous program / view)"],
      ["/", "focus tab filter"],
      ["esc", "clear filter · back to list · close"],
      ["j / k or ↓ / ↑", "move selection"],
      ["h / l or ← / →", "switch pane (split views)"],
      ["enter", "open selection · search signal"],
      ["t / shift+t", "settings / cycle theme"],
      ["?", "this help"],
    ];
    body.innerHTML = rows.map(function (r) {
      return '<div class="static-row"><span class="name">' + BV.esc(r[1]) +
        "</span><span><kbd>" + BV.esc(r[0]) + "</kbd></span></div>";
    }).join("");
    BV.modal("keyboard shortcuts", body);
  }
  BV.helpOverlay = helpOverlay;

  document.addEventListener("keydown", function (e) {
    if (BV.modalOpen()) return;        /* modal traps its own keys */

    /* Ctrl+K works even while typing */
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (BV.focusGlobalSearch) BV.focusGlobalSearch();
      return;
    }

    /* Ctrl+Tab cycles OPEN BACKUPS, browser-style - also while typing, since
       that is what a browser does. Inside the edit workspace the screen's own
       handler claims it first (there it cycles that pane's program tabs) and
       stops propagation, so this never double-fires. */
    if (e.ctrlKey && e.key === "Tab" && BV.session && BV.session.list.length > 1) {
      e.preventDefault();
      var list = BV.session.list;
      var at = -1;
      list.forEach(function (t, i) { if (t.sid === BV.session.currentSid) at = i; });
      var next = list[((at < 0 ? 0 : at) + (e.shiftKey ? -1 : 1) + list.length) % list.length];
      if (next && next.sid !== BV.session.currentSid) BV.session.switchTo(next.sid);
      return;
    }
    if (typing()) return;

    /* positional tabs follow the number row past 9: 1-9, then - and = (the
       same list buildTabbar badges from, so key and badge always agree —
       hidden always-on tabs like search/compare can never soak up a number) */
    if (!e.ctrlKey && !e.altKey) {
      var idx = -1;
      if (e.key >= "1" && e.key <= "9") idx = parseInt(e.key, 10) - 1;
      else if (e.key === "-") idx = 9;
      else if (e.key === "=") idx = 10;
      if (idx >= 0) {
        var pos = BV.positionalTabs();
        if (pos[idx]) location.hash = "#" + pos[idx].id;
        return;
      }
    }
    /* 0 is pinned to the 3d view (its tab badge shows 0), not positional */
    if (e.key === "0" && !e.ctrlKey && !e.altKey) {
      var t3 = BV.tabs.filter(function (t) { return t.id === "view3d"; })[0];
      if (t3 && BV.tabEnabled(t3)) location.hash = "#view3d";
      return;
    }

    switch (e.key) {
      case "/":
        if (liveSearch()) { e.preventDefault(); liveSearch().focus(); }
        break;
      case "j":
      case "ArrowDown":
        if (liveVT()) { e.preventDefault(); liveVT().moveSelection(1); }
        break;
      case "k":
      case "ArrowUp":
        if (liveVT()) { e.preventDefault(); liveVT().moveSelection(-1); }
        break;
      case "h":
      case "ArrowLeft":
        if (liveVT() && liveVT().switchPane) { e.preventDefault(); liveVT().switchPane(-1); }
        break;
      case "l":
      case "ArrowRight":
        if (liveVT() && liveVT().switchPane) { e.preventDefault(); liveVT().switchPane(1); }
        break;
      case "Enter":
        if (liveVT()) { liveVT().openSelected(); }
        break;
      case "Escape": {
        /* back out of detail routes */
        var parts = location.hash.slice(1).split("/");
        if (parts.length > 1 && (parts[0] === "programs" || parts[0] === "files")) {
          location.hash = "#" + parts[0];
        }
        break;
      }
      case "Backspace":
        /* hash routing gives us real history: walk back through previously
           viewed programs / searches / tabs */
        e.preventDefault();
        history.back();
        break;
      case "t":
        BV.uiPrefs.modal();          /* lands on display — theme is its first row */
        break;
      case "T":
        BV.theme.cycle();
        break;
      case "?":
        helpOverlay();
        break;
    }
  });
})();
