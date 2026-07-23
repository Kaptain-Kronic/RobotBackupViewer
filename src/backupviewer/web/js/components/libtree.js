/* components/libtree.js - ONE library tree for every robot listing.
   The home screen and the compare-from-library picker show the same saved
   library, so this component owns the STRUCTURE they share: grouping into
   PLANT -> LINE folders (the '—' no-name bucket pinned last), robot sort
   order, collapsible folders (left-click toggles, right-click folds the
   subtree), the filter semantics (a matching plant/line name keeps its whole
   group; groups render forced-open while a filter is live and the remembered
   fold state comes back when it clears), and the empty-folder skeleton notes.
   Callers own the ROWS and what happens on them: what a robot row looks like,
   what clicking one does, extra line-head controls (the library's select-all
   boxes), and when to re-render. Fold state lives on the instance — a
   module-level instance keeps it across re-renders and screen remounts. */
(function () {
  "use strict";

  /* group keys alphabetically with the '—' (no plant/line) bucket pinned last */
  function groupKeys(obj) {
    return Object.keys(obj).sort(function (a, b) {
      if (a === "—") return 1;
      if (b === "—") return -1;
      return a.localeCompare(b);
    });
  }

  function nameCmp(a, b) { return (a.robot || "").localeCompare(b.robot || ""); }

  function defaultMatches(r, q) {
    return [r.robot || "", r.model || "", r.notes || "", (r.ips || []).join(" ")]
      .join(" ").toLowerCase().indexOf(q) >= 0;
  }

  /* opts (all per-instance):
       row(r, nested) -> node  REQUIRED: one robot row, click behavior included
                               (nested = a camera indented under its linked robot)
       lineExtras(ln, lineRobots, key) -> node|null   appended to a line head
                               (home hangs its select-all checkbox here)
       matches(r, q) -> bool   row filter (default: robot/model/IP/notes text;
                               BV.libTree.defaultMatches is exposed so a custom
                               matcher can extend it instead of re-listing fields)
       noun: "cameras"         what the no-match note calls the rows ("robots")
       skeleton: true          show the "empty plant/line folder" notes (the
                               files-are-law skeleton; leave off for pickers)
       counts: true            robot-count badge on plant + line heads (for
                               mostly-folded views where rows are out of sight)
       lineBodyClass: "x"      extra class on every line body (the multi-cam
                               view turns line bodies into a tile grid)
       startOpen(key, kind) -> bool   first-seen fold state, kind 'plant'|'line'
                               (default open); user toggles are remembered over it
     render(body, data, ropts) — data {robots, emptyPlants, emptyLines},
       ropts {q, cmp} (q = filter text, cmp = robot comparator; both optional).
       Returns {shown, total, visible} — visible is the rendered robots in
       render order, for anything order-sensitive (shift+click ranges). */
  BV.libTree = function (opts) {
    opts = opts || {};
    var state = {};   /* fold state: key -> bool(open); unset = startOpen/default */

    /* persistKey: settings key to remember folds across app restarts (the
       ov_collapsed pattern - the whole key->bool object rides settings.json).
       Trees are constructed at script load, BEFORE the boot chain has fetched
       settings - so hydration is lazy, on the first render (renders only
       happen post-boot). User toggles debounce-write the object back.
       Pickers and other transient trees just omit the opt. */
    var _hydrated = !opts.persistKey;
    function hydrate() {
      if (_hydrated) return;
      _hydrated = true;
      var saved = (BV.state.settings || {})[opts.persistKey];
      if (saved && typeof saved === "object") {
        Object.keys(saved).forEach(function (k) {
          if (!(k in state)) state[k] = !!saved[k];   /* live toggles win */
        });
      }
    }
    var saveState = opts.persistKey ? BV.debounce(function () {
      if (BV.state.settings) BV.state.settings[opts.persistKey] = state;
      BV.api.call("set_setting", opts.persistKey, state).catch(function () {});
    }, 500) : null;

    function isOpen(key, kind) {
      if (key in state) return state[key];
      return opts.startOpen ? !!opts.startOpen(key, kind) : true;
    }

    function fold(node, head, body, key, kind, forceOpen) {
      node.appendChild(head);
      node.appendChild(body);
      BV.collapsible(node, head, body, {
        /* a filtered render forces groups open so matches are never folded
           away; only USER toggles write, so the fold state returns on clear */
        open: forceOpen || isOpen(key, kind),
        onToggle: function (open) {
          state[key] = open;
          if (saveState) saveState();
        },
      });
      return node;
    }

    function countSpan(head) {
      if (!opts.counts) return null;
      var c = BV.el("span", { class: "lib-count" });
      head.appendChild(c);
      return c;
    }

    return {
      render: function (body, data, ropts) {
        hydrate();   /* pull persisted folds now that settings are loaded */
        ropts = ropts || {};
        var q = (ropts.q || "").toLowerCase();
        var cmp = ropts.cmp || nameCmp;
        var matches = opts.matches || defaultMatches;

        var plants = {};
        ((data && data.emptyPlants) || []).forEach(function (pl) {
          plants[pl || "—"] = plants[pl || "—"] || {};
        });
        ((data && data.emptyLines) || []).forEach(function (g) {
          var pl = g.plant || "—", ln = g.line || "—";
          plants[pl] = plants[pl] || {};
          plants[pl][ln] = plants[pl][ln] || [];
        });
        ((data && data.robots) || []).forEach(function (r) {
          var pl = r.plant || "—", ln = r.line || "—";
          plants[pl] = plants[pl] || {};
          plants[pl][ln] = plants[pl][ln] || [];
          plants[pl][ln].push(r);
        });

        body.innerHTML = "";
        var visible = [], total = 0, shown = 0;
        groupKeys(plants).forEach(function (pl) {
          var plMatch = !!q && pl.toLowerCase().indexOf(q) >= 0;
          var plantNode = BV.el("div", { class: "lib-plant" });
          var plantHead = BV.el("div", { class: "lib-plant-h" }, BV.esc(pl));
          var plantCount = countSpan(plantHead);
          var plantBody = BV.el("div", { class: "lib-plant-body" });
          var lines = plants[pl];
          var lineKeys = groupKeys(lines);
          var renderedLines = 0, plantRobots = 0;
          if (opts.skeleton && !lineKeys.length && (!q || plMatch)) {
            plantBody.appendChild(BV.el("div", { class: "dim lib-empty-note" },
              "empty plant folder — add line folders inside"));
          }
          lineKeys.forEach(function (ln) {
            total += lines[ln].length;
            /* a matching plant/line name keeps its whole group; otherwise only
               matching robots survive, and a line with none drops out */
            var lnMatch = plMatch || (!!q && ln.toLowerCase().indexOf(q) >= 0);
            var lineRobots = (q && !lnMatch) ? lines[ln].filter(function (r) {
              return matches(r, q);
            }) : lines[ln];
            if (q && !lnMatch && !lineRobots.length) return;
            lineRobots = lineRobots.slice().sort(cmp);
            shown += lineRobots.length;
            plantRobots += lineRobots.length;
            var key = pl + "|||" + ln;
            var lineNode = BV.el("div", { class: "lib-line" });
            var lineHead = BV.el("div", { class: "lib-line-h" });
            lineHead.appendChild(BV.el("span", { class: "lib-line-name" }, BV.esc(ln)));
            var lineCount = countSpan(lineHead);
            if (lineCount) lineCount.textContent = String(lineRobots.length);
            if (opts.lineExtras) {
              var extras = opts.lineExtras(ln, lineRobots, key);
              if (extras) lineHead.appendChild(extras);
            }
            var lineBody = BV.el("div", { class: "lib-line-body" +
              (opts.lineBodyClass ? " " + opts.lineBodyClass : "") });
            if (opts.skeleton && !lineRobots.length) {
              lineBody.appendChild(BV.el("div", { class: "dim lib-empty-note" },
                "empty line folder — no robot folders yet"));
            }
            /* nest linked cameras under the robot they inspect (same line);
               unlinked cameras and cross-line links stay at top level so
               nothing disappears. visible gets the NESTED order — it must
               match what the user sees for shift+click ranges */
            var lineRobotIds = {};
            lineRobots.forEach(function (r) {
              if ((r.device_type || "robot") === "robot") lineRobotIds[r.id] = 1;
            });
            lineRobots.forEach(function (r) {
              var isCam = (r.device_type || "").indexOf("camera") === 0;
              if (isCam && r.linked_robot_id && lineRobotIds[r.linked_robot_id]) return;
              visible.push(r);
              lineBody.appendChild(opts.row(r));
              lineRobots.forEach(function (c) {
                if ((c.device_type || "").indexOf("camera") === 0 && c.linked_robot_id === r.id) {
                  visible.push(c);
                  lineBody.appendChild(opts.row(c, true));
                }
              });
            });
            fold(lineNode, lineHead, lineBody, key, "line", !!q);
            plantBody.appendChild(lineNode);
            renderedLines++;
          });
          if (q && !renderedLines && !plMatch) return;   /* nothing here matches */
          if (plantCount) plantCount.textContent = String(plantRobots);
          fold(plantNode, plantHead, plantBody, pl, "plant", !!q);
          body.appendChild(plantNode);
        });
        if (q && !body.childNodes.length) {
          body.innerHTML = '<div class="empty-lib">no ' + (opts.noun || "robots") +
            ' match “' + BV.esc(ropts.q) +
            '” — clear the filter to see the library.</div>';
        }
        return { shown: shown, total: total, visible: visible };
      },
    };
  };

  /* the default row matcher, shared so a caller's matches() can extend it
     (match MORE than these fields) without copying the field list */
  BV.libTree.defaultMatches = defaultMatches;
})();
