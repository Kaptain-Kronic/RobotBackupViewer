/* alarms.js - no longer a tab: renders the alarm history section embedded in
   Overview's "at backup time" area. Snapshot of recent alarms by default,
   expandable to the full virtualized, filterable table. */
(function () {
  "use strict";

  var SNAPSHOT_ROWS = 15;

  var FACILITY_CLASS = {
    SRVO: "err", SYST: "warn", MOTN: "err", INTP: "err", APSH: "warn",
    TPIF: "ghost", HOST: "ghost", FILE: "ghost", SVGN: "warn",
  };

  function codePill(r) {
    if (!r.code) return '<span class="dim">—</span>';
    return BV.pill(r.code, FACILITY_CLASS[r.facility] || "ghost");
  }

  function renderInto(root) {
    BV.api.call("get_alarm_files").then(function (files) {
      if (!files.length) {
        root.innerHTML = '<div class="dim" style="font-size:.8rem">no alarm files in this backup</div>';
        return;
      }
      var cur = files.find(function (f) { return f.file.toUpperCase() === "ERRALL.LS"; }) || files[0];
      var expanded = false;

      var head = BV.el("div", { style: "display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin-bottom:.5rem" });
      head.insertAdjacentHTML("beforeend", '<h3 style="margin:0;font-size:.78rem;color:var(--sub);text-transform:lowercase">alarm history</h3>');
      var seg = BV.el("div", { class: "seg" });
      files.forEach(function (f) {
        var short = f.file.replace(/^ERR/i, "").replace(/\.LS$/i, "").toLowerCase() || "all";
        var b = BV.el("button", { class: f.file === cur.file ? "active" : "", title: f.file },
          BV.esc(short) + '<span class="cnt">' + f.rows + "</span>");
        b.addEventListener("click", function () {
          cur = f;
          seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
          b.classList.add("active");
          draw();
        });
        seg.appendChild(b);
      });
      head.appendChild(seg);
      var expandBtn = BV.el("button", { class: "btn" }, "show all");
      expandBtn.addEventListener("click", function () {
        expanded = !expanded;
        expandBtn.textContent = expanded ? "show recent" : "show all";
        draw();
      });
      head.appendChild(expandBtn);
      root.appendChild(head);

      var body = BV.el("div");
      root.appendChild(body);

      function drawSnapshot() {
        BV.api.call("get_alarms", cur.file, 0, SNAPSHOT_ROWS, "").then(function (res) {
          body.innerHTML = "";
          body.appendChild(BV.table([
            { key: "datetime", label: "time", dim: true },
            { key: "code", label: "code", render: codePill },
            { key: "message", label: "message" },
            { key: "act", label: "", render: function (r) { return r.active ? BV.pill("act", "err") : ""; } },
          ], res.rows));
          if (res.total > SNAPSHOT_ROWS) {
            body.insertAdjacentHTML("beforeend",
              '<div class="dim" style="font-size:.75rem;margin-top:.3rem">showing ' +
              res.rows.length + " of " + res.total + " — “show all” for the rest</div>");
          }
        }).catch(function (e) {
          body.innerHTML = '<div class="dim">' + BV.esc(e.message) + "</div>";
        });
      }

      var vt = null;
      function drawFull() {
        body.innerHTML = "";
        var sb = BV.searchBox({
          placeholder: "filter alarms…",
          onChange: function (q) { if (vt) vt.setFilter(q); },
        });
        sb.el.style.marginBottom = "0.5rem";
        body.appendChild(sb.el);
        var host = BV.el("div", { style: "height:430px" });
        body.appendChild(host);
        vt = new BV.VTable(host, {
          columns: [
            { key: "seq", label: "#", width: 80, num: true, dim: true },
            { key: "datetime", label: "time", width: 178, dim: true },
            { key: "code", label: "code", width: 110, render: codePill },
            { key: "message", label: "message", grow: true, render: function (r) {
                return BV.esc(r.message) + (r.cause ? ' <span class="dim">(' + BV.esc(r.cause) + ")</span>" : "");
              } },
            { key: "severity", label: "severity", width: 100, dim: true },
            { key: "active", label: "", width: 60, render: function (r) {
                return r.active ? BV.pill("act", "err") : "";
              } },
          ],
          provider: function (offset, limit, query) {
            return BV.api.call("get_alarms", cur.file, offset, limit, query || "");
          },
          onMeta: function (res) { sb.setCount(res.filtered, res.total); },
        });
      }

      function draw() {
        if (vt) { vt.destroy(); vt = null; }
        body.innerHTML = "";
        if (expanded) drawFull();
        else drawSnapshot();
      }
      draw();
    }).catch(function (e) {
      root.innerHTML = '<div class="dim">' + BV.esc(e.message) + "</div>";
    });
  }

  BV.alarms = { renderInto: renderInto };
})();
