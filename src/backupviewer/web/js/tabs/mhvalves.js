/* tabs/mhvalves.js - GM material-handling gripper / valve config (MHGRIPDT.VA).
   A compact per-gripper summary (name + the signals that drive/sense it) up
   top, then the full untouched config as a collapsible nested tree (headers on
   headers) reusing the system-vars tree. The signal numbers are shown raw -
   they're a GM-specific namespace, not direct DI/DO indices. */
(function () {
  "use strict";

  function gripperCard(g) {
    /* aggregate the signal numbers by role, keeping first-seen order */
    var byRole = {}, order = [];
    (g.signals || []).forEach(function (s) {
      if (!(s.role in byRole)) { byRole[s.role] = []; order.push(s.role); }
      byRole[s.role].push(s.index);
    });
    var pairs = order.map(function (r) { return [r, byRole[r].join(", ")]; });

    var co = BV.card({
      title: g.name,
      count: "tool " + g.tool + "." + g.num,
      collapsible: true,
    });
    if (g.vacuum) co.head.insertAdjacentHTML("beforeend", " " + BV.pill("vacuum", "ghost"));
    co.head.addEventListener("click", function () { co.el.classList.toggle("collapsed"); });
    co.body.appendChild(BV.kv(pairs));
    return co.el;
  }

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    BV.api.call("get_mhvalves").then(function (d) {
      var grips = d.grippers || [];

      var sb = BV.searchBox({
        placeholder: "filter the full config…",
        onChange: function (q) { applyFilter(q); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      view.insertAdjacentHTML("beforeend",
        '<div class="hero" style="padding-bottom:.4rem"><span class="robot-name" style="font-size:1.2rem">mh valves</span>' +
        '<span class="hero-sub">' + grips.length + " gripper" + (grips.length === 1 ? "" : "s") +
        " configured · signal numbers are GM valve-table refs, shown raw</span></div>");

      if (grips.length) {
        var cards = BV.el("div", { class: "cards", style: "grid-template-columns:repeat(auto-fill,minmax(240px,1fr));margin-bottom:1.2rem" });
        grips.forEach(function (g) { cards.appendChild(gripperCard(g)); });
        view.appendChild(cards);
      } else {
        view.insertAdjacentHTML("beforeend",
          '<div class="dim" style="padding:.3rem 0 1rem">no configured grippers in this backup</div>');
      }

      /* full untouched config, as the system-vars nested tree */
      view.insertAdjacentHTML("beforeend",
        '<h3 style="font-size:.78rem;color:var(--sub);text-transform:lowercase;margin:.4rem 0 .5rem">full configuration</h3>');
      var list = BV.el("div", { class: "sysvar-list" });
      view.appendChild(list);
      var nodes = (d.records || []).map(function (rec) {
        var el = BV.sysvars.treeNode(rec, "sysvar-node");
        return { el: el, name: (rec.name || "").toLowerCase() };
      });
      nodes.forEach(function (n) { list.appendChild(n.el); });

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
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">mh valves unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "mhvalves", label: "mh valves", render: render });
})();
