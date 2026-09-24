/* components/poscard.js - BV.pos: the ONE place a taught position turns into
   pixels. Two renderings of the same group list:

     BV.pos.summary(groups)     -> html: one compact line for a table cell
     BV.pos.card(groups, opts)  -> element: every axis of ONE group at full
                                   precision, with ‹ › paging across the groups
                                   a multi-group position carries
     BV.pos.taught(groups)      -> true when any group holds data

   groups: [{gp|group, kind: "joint"|"cartesian"|"uninit", joints: [...],
             x..r, ext: [{n, value, unit}], config, uf, ut, masked}] - exactly
   the dicts parse_posreg / parse_ls_program produce, one per motion group.

   Why one primitive: position registers (registers tab) and taught points
   (program detail) showed the same numbers through two different formatters,
   and both dropped extended axes (a rail's E1) on the floor. One formatter
   means every screen reads the same and gains the same fixes.

   The group number is deliberately NOT a column anywhere: a multi-group
   position is one point taught for several groups, so the groups are pages
   of the expanded card, not rows of the table. Only TAUGHT groups make
   pages: a two-group robot writes a group-2 line for every one of its 1200
   registers and nearly all of them are Uninitialized, so those are named in
   one dim chip under the data ("gp 2 untaught") rather than paged through.

   opts: { page: initial group index, onPage(i): the caller remembers it,
           actions: [{label, title, onClick(group, pageIndex)}] - small
           buttons in the card head } */
(function () {
  "use strict";

  var CART = ["x", "y", "z", "w", "p", "r"];

  function gpNum(g) { return g.gp !== undefined ? g.gp : g.group; }
  function isTaught(g) { return !!g && (g.kind === "joint" || g.kind === "cartesian"); }
  function fmt(v, digits) { return v === null || v === undefined ? "—" : BV.fmt.num(v, digits); }

  function taught(groups) {
    return (groups || []).some(isTaught);
  }

  /* every numeric value of a group; a masked group with nothing else to show
     reads "masked" rather than a row of dashes */
  function numbers(g) {
    var out = [];
    if (g.kind === "joint") out = (g.joints || []).slice();
    else if (g.kind === "cartesian") out = CART.map(function (ax) { return g[ax]; });
    (g.ext || []).forEach(function (e) { out.push(e.value); });
    return out;
  }

  function summary(groups) {
    groups = groups || [];
    var pages = groups.filter(isTaught);
    var g = pages[0] || groups[0];
    var html = "";
    if (pages.length > 1) {
      html += '<span class="pill ghost pos-ngrp" title="taught for ' + pages.length +
        ' motion groups — expand to page through them">' + pages.length + " grp</span> ";
    }
    if (!g) return html + '<span class="dim">—</span>';
    if (!isTaught(g)) {
      return html + '<span class="dim">' + (g.kind === "uninit" ? "uninitialized" : "—") + "</span>";
    }
    if (g.masked && numbers(g).every(function (v) { return v === null || v === undefined; })) {
      return html + '<span class="dim">masked ********</span>';
    }
    /* a masked axis reads as masked, not as the dash an absent value gets */
    function one(v, digits) { return v === null || v === undefined ? (g.masked ? "****" : "—") : BV.fmt.num(v, digits); }
    if (g.kind === "joint") {
      html += '<span class="dim">J</span> ' + (g.joints || []).map(function (j) {
        return one(j, 2);
      }).join(", ");
    } else {
      html += CART.map(function (ax) {
        return '<span class="dim">' + ax + "</span>" + one(g[ax], 1);
      }).join(" ");
    }
    (g.ext || []).forEach(function (e) {
      html += ' <span class="dim">e' + e.n + "</span>" + one(e.value, 1);
    });
    return html;
  }

  function card(groups, opts) {
    opts = opts || {};
    var all = groups || [];
    /* pages = the taught groups; an all-untaught position shows its first
       group's "uninitialized" as the one page */
    groups = all.filter(isTaught);
    if (!groups.length) groups = all.slice(0, 1);
    var untaught = all.filter(function (g) { return groups.indexOf(g) < 0; });
    var el = BV.el("div", { class: "pos-card" });
    var page = Math.max(0, Math.min(groups.length - 1, opts.page || 0));
    var head = BV.el("div", { class: "pos-head" });
    var body = BV.el("div", { class: "pos-axes" });
    el.appendChild(head);
    el.appendChild(body);
    var pager = null;
    if (groups.length > 1) {
      pager = BV.el("div", { class: "pos-pager" });
      el.appendChild(pager);
    }
    if (untaught.length) {
      /* said once, dim, under the data - never a page, never a pill */
      el.appendChild(BV.el("div", { class: "pos-untaught dim" },
        untaught.map(function (g) { return "gp " + BV.esc(gpNum(g)) + " untaught"; }).join(" · ")));
    }

    function axis(label, v, unit, masked) {
      var s = BV.el("span", { class: "pos-ax" + (masked ? " masked" : "") });
      var val = (v === null || v === undefined) ? (masked ? "********" : "—") : BV.fmt.num(v, 3);
      s.innerHTML = '<span class="lbl">' + BV.esc(label) + '</span><span class="val">' + BV.esc(val) +
        "</span>" + (unit ? '<span class="unit">' + BV.esc(unit) + "</span>" : "");
      return s;
    }

    function go(i) {
      if (i < 0 || i >= groups.length) return;
      page = i;
      if (opts.onPage) opts.onPage(i);
      paint();
    }

    function paint() {
      var g = groups[page];
      head.innerHTML = "";
      body.innerHTML = "";
      if (!g) {
        body.innerHTML = '<span class="dim">no data</span>';
        return;
      }
      var h = "";
      if (isTaught(g)) h += BV.pill(g.kind, "ghost");
      if (g.masked) h += " " + BV.pill("masked", "warn");
      if (g.uf !== undefined || g.ut !== undefined) {
        h += '<span class="pos-meta">uf ' + BV.esc(g.uf === undefined ? "—" : g.uf) +
          " · ut " + BV.esc(g.ut === undefined ? "—" : g.ut) + "</span>";
      }
      if (g.config) h += '<span class="pos-meta">config ' + BV.esc(g.config) + "</span>";
      head.innerHTML = h;
      (opts.actions || []).forEach(function (a) {
        var b = BV.el("button", { class: "btn pos-act", title: a.title || "" }, BV.esc(a.label));
        b.addEventListener("click", function (ev) {
          ev.stopPropagation();
          a.onClick(g, page);
        });
        head.appendChild(b);
      });

      if (g.kind === "joint") {
        (g.joints || []).forEach(function (j, i) {
          body.appendChild(axis("J" + (i + 1), j, "deg", g.masked && j === null));
        });
      } else if (g.kind === "cartesian") {
        CART.forEach(function (ax, i) {
          body.appendChild(axis(ax.toUpperCase(), g[ax], i < 3 ? "mm" : "deg",
            g.masked && (g[ax] === null || g[ax] === undefined)));
        });
      } else {
        body.appendChild(BV.el("span", { class: "dim" },
          g.kind === "uninit" ? "uninitialized" : "no data"));
      }
      /* extended axes after the group's own: a rail is E1 in either rep */
      (g.ext || []).forEach(function (e) {
        body.appendChild(axis("E" + e.n, e.value, e.unit, g.masked && e.value === null));
      });

      if (pager) {
        pager.innerHTML = "";
        var prev = BV.el("button", { class: "btn pos-prev", title: "previous group" }, "‹");
        var next = BV.el("button", { class: "btn pos-next", title: "next group" }, "›");
        prev.disabled = page === 0;
        next.disabled = page === groups.length - 1;
        prev.addEventListener("click", function (ev) { ev.stopPropagation(); go(page - 1); });
        next.addEventListener("click", function (ev) { ev.stopPropagation(); go(page + 1); });
        pager.appendChild(prev);
        pager.appendChild(BV.el("span", { class: "pos-page" },
          "group " + BV.esc(gpNum(g)) + ' <span class="dim">· ' + (page + 1) + "/" + groups.length + "</span>"));
        pager.appendChild(next);
      }
    }

    paint();
    return el;
  }

  BV.pos = { summary: summary, card: card, taught: taught };
})();
