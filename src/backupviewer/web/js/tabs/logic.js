/* tabs/logic.js - "logic": what a technician wrote inside a Keyence CV-X
   inspection program. Two panes: a rail of the calculation scripts (grouped
   under their program when the backup holds more than one) over the program's
   name list, and the selected script as code on the right.

   Everything shown here is read out of setting/<NNN>/inspect.dat by
   parsers/cvx_program.py (api.cvx_logic), and the limits that parser proved
   travel with the data into this screen:
   - NOTHING ties a name record to a tool number, so no row here is ever
     labelled "tool 5" and the names are not numbered or ordered by tool;
   - the name list mixes the vendor's built-in tool-type vocabulary ("Color
     Detection", "Edge Pitch" ship inside every program) with the names a
     technician typed, and no discriminator has been proven - so the list says
     so in one plain sentence instead of filtering on a guess;
   - a script's title is its own first comment line, blank when it has none -
     never invented.

   Load order: util.js, api.js, state.js, components/pill.js +
   components/builders.js before this file (index.html). */
(function () {
  "use strict";

  /* a Calculation unit names itself in its own first comment - the shop's
     convention, and the only signal that separates the units the controller
     lists from helper expressions in the same block */
  var CALC_TITLE = /calculation/i;

  /* the language's own cross-tool reference - Tnnn.RSLT.MNEMONIC[i]:MS. It is
     the ONE token worth an accent: it says "this line reads another tool's
     result", which is the thing a troubleshooter is hunting. Everything else
     stays default text - a rainbow highlighter would be inventing categories
     the format has not been proven to have. */
  var REF = /T\d+\.RSLT\.[A-Za-z_]\w*(?:\[\d+\])?(?::[A-Za-z]+)?/g;

  function isComment(line) {
    return line.replace(/^\s+/, "").charAt(0) === "'";
  }

  /* one source line -> html. The reference pattern contains none of the five
     characters BV.esc rewrites, and an entity it produced (&#39;) can never
     contain one either, so matching AFTER escaping is safe. */
  function lineHtml(line) {
    var html = BV.esc(line);
    if (isComment(line)) return '<span style="color:var(--sub)">' + html + "</span>";
    return html.replace(REF, function (m) {
      return '<span style="color:var(--accent)">' + m + "</span>";
    });
  }

  function lineCount(n) { return n + " line" + (n === 1 ? "" : "s"); }

  /* "program 001 · <name>" - the name is the program's own, read by
     cvx_inspect; it is blank when the file carries none, and nothing is
     substituted for it */
  function progLabel(p) {
    var base = p.program ? "program " + p.program : "program";
    return p.name ? base + " · " + p.name : base;
  }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    BV.api.call("cvx_logic").then(function (data) {
      var progs = (data && data.programs) || [];
      if (!progs.length) {
        /* the tab is gated on a program that parsed (session._tab_need
           "*cvxlogic"), so this is the hand-typed-hash fallback */
        view.innerHTML = '<div class="empty-state"><div class="big">no inspection logic</div>' +
          '<div class="hint">no program in this backup opened as one</div></div>';
        return;
      }
      var s = BV.tabState("logic");
      /* the shared two-pane flex host (rail + main, each with its own scroll) -
         same layout primitive the 3d screens use, not a private copy */
      view.classList.add("v3-host");

      /* the class names carry no CSS - they are the stable hooks a probe (and
         any later stylesheet) addresses, the same way cvx3d-row is */
      var rail = BV.el("aside", { class: "logic-rail", style:
        "flex:0 0 20rem;max-width:45%;min-height:0;overflow-y:auto;padding:.25rem .1rem 1rem" });
      var main = BV.el("div", { class: "logic-main", style:
        "flex:1 1 auto;min-width:0;min-height:0;display:flex;flex-direction:column;gap:.5rem" });
      var head = BV.el("div", { class: "logic-head", style:
        "flex:none;display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap;min-height:1.9rem" });
      /* .viewer is the app's code box (own scroll, selectable); height:auto
         overrides its height:100% so it fits BESIDE the header in this column */
      var code = BV.el("div", { class: "viewer logic-code", style:
        "flex:1 1 auto;min-height:0;height:auto" });
      main.appendChild(head);
      main.appendChild(code);
      view.appendChild(rail);
      view.appendChild(main);

      var cur = null;
      var rows = {};   /* rowKey -> rail row element */

      /* the tab strip's other screens build any compare control themselves
         (programs.js mounts its own "vs" button); nothing is automatic, and a
         camera's inspection logic has no diff view to reach yet - so the
         toolbar carries the copy button alone. */
      var copyBtn = BV.el("button", { class: "btn",
        title: "copy this script to the clipboard" }, "copy script");
      copyBtn.addEventListener("click", function () {
        if (!cur || !cur.sc) return;
        BV.copyText(cur.sc.text, "script copied");
      });
      toolbar.appendChild(copyBtn);

      function keyOf(e) { return e.p.rel + BV.KEYSEP + e.i; }

      function markSelected(e) {
        var want = keyOf(e);
        Object.keys(rows).forEach(function (k) {
          var on = k === want;
          rows[k].style.background = on ? "var(--bg2)" : "";
          rows[k].style.boxShadow = on ? "inset 2px 0 0 var(--accent)" : "";
        });
      }

      /* the code pane. Lines are printed exactly as the endpoint returned them
         (the parser hands them back already stripped, so there is no
         indentation to restore and none is invented); pre-wrap keeps whatever
         whitespace and blank lines DO arrive instead of collapsing them. */
      function show(e, keepScroll) {
        head.innerHTML = "";
        var title = e.sc ? (e.sc.title || "untitled") : "no calculation script";
        head.appendChild(BV.el("span", { title: title, style:
          "font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;" +
          "white-space:nowrap" }, BV.esc(title)));
        var bits = [];
        if (e.sc) bits.push(lineCount(e.sc.lines));
        if (progs.length > 1) bits.push(progLabel(e.p));
        bits.push(e.p.rel);
        head.insertAdjacentHTML("beforeend",
          '<span style="color:var(--sub);font-size:.82rem;min-width:0;' +
          'overflow-wrap:break-word">' + BV.esc(bits.join(" · ")) + "</span>");

        copyBtn.disabled = !e.sc;
        code.innerHTML = "";
        if (!e.sc) {
          code.innerHTML = '<div style="padding:1rem;color:var(--sub);font-size:.85rem;' +
            'line-height:1.5">this program carries no run of lines the parser could ' +
            "vouch for as a calculation script — the names it does carry are listed " +
            "on the left.</div>";
        } else {
          var pre = BV.el("pre");
          pre.innerHTML = e.sc.text.split("\n").map(function (ln) {
            return '<div class="code-line"><span class="lc">' + lineHtml(ln) + "</span></div>";
          }).join("");
          code.appendChild(pre);
        }
        if (!keepScroll) code.scrollTop = 0;
      }

      /* the names this program carries, deduplicated with their counts. The
         note under them is the honesty contract for this screen and travels
         WITH the list - it is not a tooltip and not a one-time toast. */
      var namesHost = BV.el("div", { class: "logic-names", style: "margin:.9rem .1rem 0" });

      function showNames(p) {
        namesHost.innerHTML = "";
        var co = BV.card({ title: "names in this program", count: p.names.length });
        if (p.names.length) {
          var html = '<div style="display:flex;flex-wrap:wrap;gap:.3rem">';
          p.names.forEach(function (n) {
            html += '<span style="display:inline-flex;align-items:center;gap:.2rem">' +
              BV.pill(n.name, "ghost") +
              (n.count > 1
                ? '<span style="color:var(--sub);font-size:.72rem">×' + n.count + "</span>"
                : "") + "</span>";
          });
          co.el.insertAdjacentHTML("beforeend", html + "</div>");
        } else {
          co.el.insertAdjacentHTML("beforeend",
            '<div style="color:var(--sub);font-size:.8rem">no names in this program</div>');
        }
        co.el.insertAdjacentHTML("beforeend",
          '<div style="color:var(--sub);font-size:.75rem;line-height:1.5;margin-top:.55rem">' +
          "this list holds the built-in tool-type names the program ships with as well " +
          "as the ones a technician typed, and nothing in the file says which tool any " +
          "of them belongs to.</div>");
        namesHost.appendChild(co.el);
      }

      function select(e, restoring) {
        cur = e;
        s.sel = { rel: e.p.rel, i: e.i };
        markSelected(e);
        show(e, !!restoring);
        showNames(e.p);
      }

      function groupHead(p, n) {
        return BV.el("div", { class: "logic-group", style:
          "margin:.7rem 0 .15rem;padding:0 .5rem;font-size:.72rem;color:var(--sub);" +
          "letter-spacing:.04em;overflow-wrap:break-word" },
          BV.esc(progLabel(p)) + ' <span style="opacity:.7">' + n + "</span>");
      }

      function railRow(e) {
        /* the row truncates, so the whole title lives in the tooltip; an
           untitled script says WHY it has none rather than looking broken */
        var r = BV.el("div", { class: "logic-row",
          title: e.sc
            ? (e.sc.title || "this script opens on no comment, so it has no title")
            : "this program carries no calculation script",
          style: "padding:.35rem .5rem;border-radius:var(--radius);cursor:pointer;min-width:0" });
        var line = BV.el("div", { style:
          "display:flex;align-items:center;gap:.4rem;min-width:0" });
        var label = '<span style="flex:1 1 auto;min-width:0;overflow:hidden;' +
          'text-overflow:ellipsis;white-space:nowrap';
        if (e.sc && e.sc.title) {
          label += '">' + BV.esc(e.sc.title) + "</span>";
        } else if (e.sc) {
          label += ';color:var(--sub)">untitled</span>';
        } else {
          label += ';color:var(--sub)">no calculation script</span>';
        }
        line.insertAdjacentHTML("beforeend", label);
        /* flex:none: the count must not be squeezed to nothing by a long title */
        if (e.sc) {
          line.insertAdjacentHTML("beforeend", '<span style="flex:none">' +
            BV.pill(lineCount(e.sc.lines), "ghost") + "</span>");
        }
        r.appendChild(line);
        r.addEventListener("click", function () { select(e); });
        rows[keyOf(e)] = r;
        return r;
      }

      /* one selectable entry per script, plus a placeholder for a program that
         carries none - its name list is still evidence, so the program never
         vanishes from the rail */
      var entries = [];
      var extras = [];        /* the ones behind the toggle */
      var extraHost = BV.el("div");
      progs.forEach(function (p) {
        if (progs.length > 1) rail.appendChild(groupHead(p, p.scripts.length));
        if (!p.scripts.length) {
          var e0 = { p: p, sc: null, i: -1 };
          entries.push(e0);
          rail.appendChild(railRow(e0));
          return;
        }
        /* A program's block holds more script-shaped text than the controller
           shows as a Calculation unit: helper expressions belonging to other
           tools sit in the same region. The technicians' own convention is the
           only thing that separates them - a Calculation unit's first comment
           names it one ("'Dual Bin 1 Calculation Redundancy", "'Path priority
           Calculation") - so that is what leads the rail. It is a CONVENTION,
           not a fact the format guarantees, which is why the rest folds behind
           a toggle instead of being dropped, and why a program whose scripts
           name none of themselves shows all of them rather than nothing. */
        var main = p.scripts.filter(function (sc) { return CALC_TITLE.test(sc.title || ""); });
        var rest = p.scripts.filter(function (sc) { return !CALC_TITLE.test(sc.title || ""); });
        if (!main.length) { main = p.scripts; rest = []; }
        main.forEach(function (sc) {
          var e = { p: p, sc: sc, i: p.scripts.indexOf(sc) };
          entries.push(e);
          rail.appendChild(railRow(e));
        });
        rest.forEach(function (sc) {
          var e = { p: p, sc: sc, i: p.scripts.indexOf(sc) };
          entries.push(e);
          extras.push(e);
          extraHost.appendChild(railRow(e));
        });
      });
      var paintOther = function () {};   /* hoisted: the restore below calls it */
      if (extras.length) {
        if (s.showOther === undefined) s.showOther = false;
        var otherBtn = BV.el("button", { class: "btn", style:
          "margin:.7rem .5rem .2rem;font-size:.78rem" });
        paintOther = function () {
          otherBtn.textContent = (s.showOther ? "hide" : "show") +
            " other script text (" + extras.length + ")";
          extraHost.style.display = s.showOther ? "" : "none";
        };
        otherBtn.addEventListener("click", function () {
          s.showOther = !s.showOther;
          paintOther();
        });
        rail.appendChild(otherBtn);
        rail.appendChild(extraHost);
        paintOther();
      }
      rail.appendChild(namesHost);

      /* restore the script this backup was left on; else the first one */
      var init = null;
      if (s.sel) {
        entries.forEach(function (e) {
          if (e.p.rel === s.sel.rel && e.i === s.sel.i) init = e;
        });
      }
      if (init && extras.indexOf(init) >= 0 && !s.showOther) {
        s.showOther = true;
        paintOther();
      }
      select(init || entries[0], !!init);

      /* both panes scroll on their own and both are remembered (a later
         selection zeroes the code pane through show(), which the listener
         records) */
      BV.persistScroll("logic-rail", rail);
      BV.persistScroll("logic-code", code);
    }).catch(function (e) {
      view.classList.remove("v3-host");
      view.innerHTML = '<div class="empty-state"><div class="big">inspection logic unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "logic", label: "logic", render: render });
})();
