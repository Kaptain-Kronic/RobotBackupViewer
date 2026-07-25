/* tabs/mhvalves.js - material-handling gripper / valve config (MHGRIPDT.VA).
   Laid out like the pendant: a tool selector, then one card per configured
   valve showing its "Setup Clamp Information" fields and its resolved input /
   output signals. Each *_SN field is resolved through the valve's signal table
   to a real DI/DO (name + number), and every signal links into the io tab.
   A magnet end-effector (KAREL-driven, not an MH valve) is surfaced in its own
   grouped section. The full untouched config tree stays at the bottom. */
(function () {
  "use strict";

  var YN = function (b) { return b ? "yes" : "no"; };
  /* NBSP-joined so compound values ("1500 ms", "no / no") are ONE unbreakable
     token: its min-content then floors the .kv value column and the grid makes
     the prose LABEL wrap instead - a value never splits across lines */
  var MS = function (n) { return (n === null || n === undefined) ? null : n + "\u00A0ms"; };
  var PAIR = function (a, b) { return YN(a) + "\u00A0/\u00A0" + YN(b); };

  /* one resolved signal as a link into the io tab (#io/jump/DI/813) */
  function sigRow(s) {
    return '<a class="mhv-sig" href="#io/jump/' + s.kind + "/" + s.number + '" ' +
      'title="signal-table index ' + s.sn + '">' +
      '<span class="mhv-sig-role">' + BV.esc(s.role) + "</span>" +
      '<span class="mhv-sig-name">' + BV.esc(s.name) + "</span>" +
      '<span class="mhv-sig-io">' + s.kind + "[" + s.number + "]</span>" +
      "</a>";
  }

  function sigGroup(title, sigs) {
    if (!sigs.length) return "";
    return '<div class="mhv-sig-group"><div class="mhv-sig-head">' + BV.esc(title) +
      "</div>" + sigs.map(sigRow).join("") + "</div>";
  }

  function valveCard(v, mem, toolNum) {
    var key = "valve:" + toolNum + ":" + v.num;
    var co = BV.card({
      title: v.name,
      count: "valve " + v.num,
      collapsible: true,
      startCollapsed: !!mem.cards[key],
      class: "mhv-valve",
    });
    co.head.insertAdjacentHTML("beforeend",
      " " + BV.pill(v.type, v.type === "vacuum" ? "acc" : "ghost"));
    co.head.addEventListener("click", function () {
      mem.cards[key] = co.el.classList.toggle("collapsed");
    });

    var st = v.setup || {};
    co.body.insertAdjacentHTML("beforeend", BV.kv.html([
      ["no. of clamps", st.clamps],
      ["no. of parts present", st.parts_present],
      ["check opened", YN(st.check_opened)],
      ["check closed", YN(st.check_closed)],
      ["operation timeout", MS(st.operation_timeout_ms)],
      ["over-stroke delay", MS(st.over_stroke_delay_ms)],
      ["continuous check", YN(st.continuous_check)],
      ["toggle retry (grip / release)", PAIR(st.retry_grip, st.retry_release)],
      ["cancel-recover (grip / release)", PAIR(st.cancel_recover_grip, st.cancel_recover_release)],
    ]));

    var sigs = sigGroup("inputs", v.inputs || []) + sigGroup("outputs", v.outputs || []);
    co.body.insertAdjacentHTML("beforeend",
      '<div class="mhv-sigs">' + (sigs ||
        '<div class="dim" style="padding:.2rem 0">no wired signals</div>') + "</div>");
    return co.el;
  }

  /* The magnet register list is the app's composable virtual table (resizable
     columns, sort, click-a-row-to-search, keyboard nav) - same as the registers
     tab. The VTable measures its container, so it must be built AFTER mount; we
     return the host and let the caller construct it. Section is non-collapsible
     so the host always has a height. */
  function magnetSection(mag) {
    var co = BV.card({
      title: "magnet gripper",
      count: mag.counts.programs + " program" + (mag.counts.programs === 1 ? "" : "s"),
    });
    co.head.insertAdjacentHTML("beforeend", " " + BV.pill("not an MH valve", "ghost"));
    co.body.insertAdjacentHTML("beforeend",
      '<div class="dim" style="font-size:.78rem;padding:.1rem 0 .5rem">' +
      "picked by a magnet end-effector - driven by KAREL programs + registers, " +
      "configured outside the MH valve tables</div>");

    var host = null;
    if ((mag.registers || []).length) {
      host = BV.el("div", { class: "mhv-reg-vt" });
      co.body.appendChild(host);
    }
    if ((mag.programs || []).length) {
      co.body.insertAdjacentHTML("beforeend",
        '<div class="mhv-prog-list">' +
        mag.programs.map(function (p) { return BV.pill(p, ""); }).join("") + "</div>");
    }
    return { el: co.el, host: host };
  }

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";

    /* which tool / cards / config branches you had open, remembered per backup
       (same store the tab's scroll position already lives in) */
    var st = BV.tabState("mhvalves");
    if (!st.open) st.open = {};
    if (!st.cards) st.cards = {};

    Promise.all([
      BV.api.call("get_mhvalves"),
      BV.api.call("get_magnet").catch(function () { return null; }),
    ]).then(function (res) {
      var d = res[0], mag = res[1];
      var tools = d.tools || [];

      view.insertAdjacentHTML("beforeend",
        '<div class="hero" style="padding-bottom:.4rem">' +
        '<span class="robot-name" style="font-size:1.2rem">mh valves</span></div>');

      /* tool selector (pendant "Select Tool") - a header line for one tool, a
         segmented control when several tools carry valves */
      var valvesEl = BV.el("div");
      var active = null;
      if (tools.length) {
        active = tools.find(function (t) { return String(t.tool) === st.tool; }) || tools[0];
      }

      function drawValves() {
        valvesEl.innerHTML = "";
        if (!active) {
          valvesEl.insertAdjacentHTML("beforeend",
            '<div class="dim" style="padding:.3rem 0 1rem">no configured valves in this backup</div>');
          return;
        }
        valvesEl.insertAdjacentHTML("beforeend",
          '<div class="mhv-tool-line">Tool ' + active.tool + "</div>");
        var cards = BV.el("div", {
          class: "cards",
          /* rem-based track minimum (≈ the old 280px at the default root) so the
             cards widen with the text-size setting instead of squishing the
             setup values into a one-character column at 20-24px text */
          style: "grid-template-columns:repeat(auto-fill,minmax(min(19rem,100%),1fr));margin-bottom:1.2rem",
        });
        (active.valves || []).forEach(function (v) { cards.appendChild(valveCard(v, st, active.tool)); });
        valvesEl.appendChild(cards);
      }

      if (tools.length > 1) {
        var seg = BV.segmented(tools.map(function (t) {
          return { id: String(t.tool), label: t.name, count: (t.valves || []).length };
        }), {
          value: String(active.tool),
          onChange: function (id) {
            st.tool = id;
            active = tools.filter(function (t) { return String(t.tool) === id; })[0];
            drawValves();
          },
        });
        toolbar.appendChild(seg.el);
      }
      view.appendChild(valvesEl);
      drawValves();

      /* magnet end-effector (grouped here even though it isn't an MH valve) */
      if (mag && mag.is_magnet) {
        var magWrap = BV.el("div", { style: "margin-bottom:1.2rem" });
        var ms = magnetSection(mag);
        magWrap.appendChild(ms.el);
        view.appendChild(magWrap);                       /* mount before the VTable measures */
        if (ms.host) {
          BV.currentVTable = new BV.VTable(ms.host, {
            columns: [
              { key: "index", label: "#", width: 95, num: true, accent: true,
                render: function (r) { return "R[" + r.index + "]"; } },
              { key: "value", label: "value", width: 120, num: true,
                render: function (r) {
                  return (r.value === null || r.value === undefined || r.value === "")
                    ? '<span class="dim">—</span>' : BV.esc(r.value);
                } },
              { key: "comment", label: "comment", grow: true,
                render: function (r) { return r.comment ? BV.esc(r.comment) : '<span class="dim">—</span>'; } },
            ],
            data: mag.registers,
            onOpen: function (r) { location.hash = "#search/" + encodeURIComponent("R[" + r.index + "]"); },
          });
        }
      }

      /* full untouched config, as the system-vars nested tree (incl. the four
         signal tables) - with a filter in the toolbar */
      var sb = BV.searchBox({
        placeholder: "filter the full config…",
        onChange: function (q) { applyFilter(q); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      view.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);text-transform:lowercase;margin:.4rem 0 .5rem">full configuration</h3>');
      var list = BV.el("div", { class: "sysvar-list" });
      view.appendChild(list);
      var nodes = (d.records || []).map(function (rec) {
        var el = BV.sysvars.treeNode(rec, "sysvar-node", st);
        list.appendChild(el);
        return { el: el, name: (rec.name || "").toLowerCase() };
      });

      function applyFilter(q) {
        q = (q || "").toLowerCase();
        var shown = 0;
        nodes.forEach(function (n) {
          var hit = !q || n.name.indexOf(q) >= 0;
          n.el.style.display = hit ? "" : "none";
          if (hit) shown++;
        });
        sb.setCount(shown, nodes.length);
      }
      sb.setCount(nodes.length, nodes.length);
      BV.persistScroll("mhvalves", document.getElementById("view"));
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">mh valves unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "mhvalves", label: "mh valves", render: render });
})();
