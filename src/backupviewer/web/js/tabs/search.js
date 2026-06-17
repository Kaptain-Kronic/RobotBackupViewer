/* tabs/search.js - backup-wide search results (#search/<query>).
   Reached from the topbar box, ctrl+k, or by clicking any signal/register
   anywhere in the app. Hidden from the tabbar. */
(function () {
  "use strict";

  function row(html, onClick) {
    var el = BV.el("div", { class: "sr-row" }, html);
    el.addEventListener("click", onClick);
    return el;
  }

  function section(view, title, count) {
    var s = BV.el("div", { class: "sr-section" });
    s.innerHTML = "<h3>" + BV.esc(title) + ' <span class="count">' + count + "</span></h3>";
    view.appendChild(s);
    return s;
  }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    var query = decodeURIComponent((params && params[0]) || "");
    /* #search/<query>/b searches the compare robot (a vs-mode signal click) */
    var side = (params && params[1] === "b" && BV.state.compare) ? "b" : "a";
    if (!query) {
      view.innerHTML = '<div class="empty-state"><div class="big">backup-wide search</div>' +
        '<div class="hint">type in the box up top (or <kbd>ctrl</kbd>+<kbd>k</kbd>) and hit enter<br>' +
        "searches programs, io, registers, frames, macros and file names<br>" +
        'signals use pendant notation: <kbd>DI[279]</kbd>, <kbd>R[151]</kbd>, <kbd>PR[1]</kbd></div></div>';
      return;
    }

    var robotName = side === "b"
      ? (BV.state.compare.robot_name || BV.state.compare.name)
      : ((BV.state.manifest && (BV.state.manifest.robot_name || BV.state.manifest.name)) || "");
    view.innerHTML = '<div class="dim" style="padding:.5rem 0">searching…</div>';
    BV.api.call("search_backup", query, side).then(function (res) {
      view.innerHTML = "";
      var head = BV.el("div", { class: "hero", style: "padding-bottom:.6rem" });
      head.innerHTML = '<span class="robot-name" style="font-size:1.2rem">' + BV.esc(res.canonical) + "</span>" +
        '<span class="hero-sub">' + res.total + " hits" +
        (robotName ? " in " + BV.esc(robotName) : "") +
        (res.canonical !== res.query ? ' · normalized from “' + BV.esc(res.query) + "”" : "") + "</span>";
      view.appendChild(head);

      if (!res.total) {
        view.insertAdjacentHTML("beforeend",
          '<div class="empty-state" style="height:50%"><div class="big">no hits</div>' +
          '<div class="hint">signals are matched in TP notation — DI[279], not DIN[279] (we normalize either)</div></div>');
        return;
      }

      /* programs */
      if (res.programs.length) {
        var totalHits = res.programs.reduce(function (a, p) { return a + p.count; }, 0);
        var sec = section(view, "programs", res.programs.length + " · " + totalHits + " lines");
        res.programs.forEach(function (p) {
          var box = BV.el("div", { class: "sr-prog" });
          var head2 = BV.el("div", { class: "sr-head" },
            '<span class="sr-name">' + BV.esc(p.program) + "</span>" +
            '<span class="sr-cnt">' + p.count + " hit" + (p.count > 1 ? "s" : "") + "</span>" +
            '<span class="sr-cnt" style="margin-left:auto">▾</span>');
          box.appendChild(head2);
          var hits = BV.el("div");
          p.hits.forEach(function (h) {
            var hr = BV.el("div", { class: "sr-hit" },
              '<span class="ln">' + h.line + '</span><span class="tx">' + BV.highlightTP(h.text) + "</span>");
            hr.addEventListener("click", function (ev) {
              ev.stopPropagation();
              location.hash = "#programs/" + encodeURIComponent(p.program + ".LS") + "/L" + h.line;
            });
            hits.appendChild(hr);
          });
          if (p.count > p.hits.length) {
            hits.appendChild(BV.el("div", { class: "sr-hit dim" },
              '<span class="ln"></span><span class="tx dim">+ ' + (p.count - p.hits.length) + " more — open the program</span>"));
          }
          box.appendChild(hits);
          head2.addEventListener("click", function () {
            hits.style.display = hits.style.display === "none" ? "" : "none";
          });
          sec.appendChild(box);
        });
      }

      /* io */
      if (res.io.length) {
        var iosec = section(view, "io signals", res.io.length);
        res.io.forEach(function (s) {
          iosec.appendChild(row(
            '<span class="accent">' + BV.esc(s.type) + "[" + s.index + "]</span>" +
            (s.state ? BV.pill(s.state.toLowerCase(), s.state === "ON" ? "on" : s.state === "OFF" ? "off" : "ghost") : "") +
            "<span>" + (s.comment ? BV.esc(s.comment) : '<span class="dim">—</span>') + "</span>" +
            (s.rack !== null && s.rack !== undefined
              ? '<span class="dim" style="margin-left:auto">rack ' + s.rack + " · slot " + s.slot + " · port " + s.port + "</span>" : ""),
            function () { location.hash = "#io/jump/" + s.type + "/" + s.index; }
          ));
        });
      }

      /* registers */
      if (res.registers.length) {
        var rsec = section(view, "registers", res.registers.length);
        var kindRoute = { R: "num", PR: "pos", SR: "str" };
        res.registers.forEach(function (r) {
          rsec.appendChild(row(
            '<span class="accent">' + BV.esc(r.kind) + "[" + r.index + "]</span>" +
            (r.value !== null && r.value !== undefined && r.value !== "" ? "<span>" + BV.esc(r.value) + "</span>" : "") +
            '<span class="dim">' + BV.esc(r.comment || "") + "</span>",
            function () { location.hash = "#registers/" + kindRoute[r.kind] + "/jump/" + r.index; }
          ));
        });
      }

      /* frames */
      if (res.frames.length) {
        var fsec = section(view, "frames", res.frames.length);
        res.frames.forEach(function (f) {
          fsec.appendChild(row(
            '<span class="accent">' + BV.esc(f.kind) + " " + f.index + "</span>" +
            '<span class="dim">group ' + BV.esc(f.group) + "</span><span>" + BV.esc(f.comment) + "</span>",
            function () { location.hash = "#frames/" + f.group; }
          ));
        });
      }

      /* macros */
      if (res.macros.length) {
        var msec = section(view, "macros", res.macros.length);
        res.macros.forEach(function (m) {
          msec.appendChild(row(
            '<span class="accent">' + BV.esc(m.name) + "</span>" +
            '<span class="dim">' + BV.esc(m.prog_name) + "</span>",
            function () { location.hash = "#programs/" + encodeURIComponent(m.prog_name + ".LS"); }
          ));
        });
      }

      /* files */
      if (res.files.length) {
        var flsec = section(view, "files", res.files.length);
        res.files.forEach(function (n) {
          flsec.appendChild(row('<span class="accent">' + BV.esc(n) + "</span>",
            function () { location.hash = "#files/" + encodeURIComponent(n); }));
        });
      }
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">search failed</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "search", label: "search", hidden: true, always: true, render: render });
})();
