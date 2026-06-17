/* tabs/sysvars.js - the controller's $-variable dump (SYSTEM.VA) as a long,
   searchable, collapsible tree. Top-level records load up front (cheap list);
   a record's body tree is fetched lazily on first expand. Arrays and structs
   within a var collapse too. Right-click any node = expand/collapse the whole
   subtree (via the standard collapsible). */
(function () {
  "use strict";

  function leafRow(node, cls) {
    var el = BV.el("div", { class: cls });
    el.innerHTML = '<span class="sysvar-name">' + BV.esc(node.name) + "</span>" +
      (node.type ? '<span class="sysvar-type">' + BV.esc(node.type) + "</span>" : "") +
      '<span class="sysvar-val">' + BV.esc(node.value !== undefined ? node.value : "") + "</span>";
    return el;
  }

  /* an indexed struct-array element ([1], [2], ...) often carries a self-
     describing field (TITLE on a DCD menu, GRIP_NAME on a gripper, ...). Surface
     it inline on the [n] header so a long list is readable without opening each
     entry. Generic: first non-empty TITLE/NAME/COMMENT/GRIP_NAME leaf child. */
  var _TITLE_FIELDS = { TITLE: 1, NAME: 1, COMMENT: 1, GRIP_NAME: 1 };
  function elementTitle(node) {
    if (!node.name || node.name.charAt(0) !== "[") return "";
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (!c.leaf || c.value == null) continue;
      if (!_TITLE_FIELDS[(c.name || "").replace(/^\$/, "").toUpperCase()]) continue;
      var v = String(c.value).trim().replace(/^'/, "").replace(/'$/, "").trim();
      if (v) return v;
    }
    return "";
  }

  /* render one node of a fetched record tree (recursive). topCls lets a
     top-level record (e.g. a KAREL .VA record) wear the same "sysvar-node"
     styling system vars use, so both tabs look alike; nested calls default to
     the indented "sysvar-tnode". */
  function treeNode(node, topCls) {
    if (node.leaf) return leafRow(node, (topCls || "sysvar-tnode") + " sysvar-leaf");
    var el = BV.el("div", { class: topCls || "sysvar-tnode" });
    var head = BV.el("div", { class: "sysvar-head" });
    var title = elementTitle(node);
    head.innerHTML = '<span class="sysvar-name">' + BV.esc(node.name) + "</span>" +
      (title ? '<span class="sysvar-title">' + BV.esc(title) + "</span>" : "") +
      (node.type ? '<span class="sysvar-type">' + BV.esc(node.type) + "</span>" : "");
    var body = BV.el("div");
    (node.children || []).forEach(function (c) { body.appendChild(treeNode(c)); });
    el.appendChild(head);
    el.appendChild(body);
    BV.collapsible(el, head, body);
    return el;
  }

  /* a top-level record row (lazy body) */
  function recordNode(r) {
    if (!r.has_children) return leafRow(r, "sysvar-node sysvar-leaf");
    var el = BV.el("div", { class: "sysvar-node" });
    var head = BV.el("div", { class: "sysvar-head" });
    head.innerHTML = '<span class="sysvar-name">' + BV.esc(r.name) + "</span>" +
      '<span class="sysvar-type">' + BV.esc(r.type) + "</span>";
    var body = BV.el("div");
    var loaded = false;
    el.appendChild(head);
    el.appendChild(body);
    BV.collapsible(el, head, body, {
      onToggle: function (open) {
        if (!open || loaded) return;
        loaded = true;
        body.innerHTML = '<div class="dim" style="padding:.3rem 1rem">loading…</div>';
        BV.api.call("get_sysvar", r.name).then(function (tree) {
          body.innerHTML = "";
          (tree.children || []).forEach(function (c) { body.appendChild(treeNode(c)); });
          if (!tree.children || !tree.children.length) {
            body.innerHTML = '<div class="dim" style="padding:.3rem 1rem">(no contents)</div>';
          }
        }).catch(function (e) {
          body.innerHTML = '<div class="dim" style="padding:.3rem 1rem">' + BV.esc(e.message) + "</div>";
        });
      },
    });
    return el;
  }

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    BV.api.call("get_sysvar_records").then(function (recs) {
      var sb = BV.searchBox({
        placeholder: "filter system vars…",
        onChange: function (q) { applyFilter(q); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      var list = BV.el("div", { class: "sysvar-list", style: "margin:0 1.25rem 1rem" });
      view.appendChild(list);

      var nodes = recs.map(function (r) {
        var el = recordNode(r);
        return { el: el, name: r.name.toLowerCase() };
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
        sb.setCount(shown, recs.length);
      }
      sb.setCount(recs.length, recs.length);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">system vars unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* shared with the programs tab for rendering a KAREL (.PC) program's
     already-loaded variable trees */
  BV.sysvars = { treeNode: treeNode };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "sysvars", label: "system vars", render: render });
})();
