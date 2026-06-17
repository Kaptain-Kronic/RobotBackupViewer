/* components/builders.js - small DOM builders for the three structures that
   were being hand-assembled in every tab: the key/value list (.kv), the card
   shell (.card), and the identity hero (.hero). They reuse the existing CSS;
   no new classes. The card builder is a PURE shell - it never wires drag,
   collapse-on-click, or zone placement; the overview owns that behaviour and
   wires it onto the returned node. */
(function () {
  "use strict";

  /* inner <dt>/<dd> pairs; drops empty values so optional fields vanish */
  function kvInner(pairs) {
    return (pairs || []).filter(function (p) {
      return p[1] !== undefined && p[1] !== null && p[1] !== "";
    }).map(function (p) {
      return "<dt>" + BV.esc(p[0]) + "</dt><dd" + (p[2] ? ' class="accent"' : "") + ">" +
        BV.esc(p[1]) + "</dd>";
    }).join("");
  }

  /* pairs: [[term, value, accent?], ...]. opts.class appends extra dl classes. */
  BV.kv = function (pairs, opts) {
    opts = opts || {};
    return BV.el("dl", { class: "kv" + (opts.class ? " " + opts.class : "") }, kvInner(pairs));
  };

  /* the same list as an HTML string, for the many call sites that build markup
     and drop it in with insertAdjacentHTML */
  BV.kv.html = function (pairs, opts) {
    opts = opts || {};
    return '<dl class="kv' + (opts.class ? " " + opts.class : "") + '">' + kvInner(pairs) + "</dl>";
  };

  /* opts: { id, title, count, collapsible, startCollapsed, class, headTitle, body }
     returns { el, head, body, setCollapsed(bool) }. body === el, so callers just
     append their content to either. */
  BV.card = function (opts) {
    opts = opts || {};
    var cls = "card" +
      (opts.collapsible ? " collapsible" : "") +
      (opts.startCollapsed ? " collapsed" : "") +
      (opts.class ? " " + opts.class : "");
    var el = BV.el("div", { class: cls });
    if (opts.id !== undefined && opts.id !== null) el.dataset.cardId = opts.id;
    var titleHtml = BV.esc(opts.title);
    if (opts.count !== undefined && opts.count !== null && opts.count !== "") {
      titleHtml += ' <span class="count">' + BV.esc(opts.count) + "</span>";
    }
    var head = BV.el("h3", opts.headTitle ? { title: opts.headTitle } : null, titleHtml);
    el.appendChild(head);
    if (opts.body !== undefined && opts.body !== null) {
      if (typeof opts.body === "string") el.insertAdjacentHTML("beforeend", opts.body);
      else el.appendChild(opts.body);
    }
    return {
      el: el,
      head: head,
      body: el,
      setCollapsed: function (v) { el.classList.toggle("collapsed", v); },
    };
  };

  /* opts: { name, model, sub:[parts], stick, chips, class }
     sub parts are joined with the same dot separator the overview hero uses;
     chips is an HTML string or node appended after the identity line. */
  BV.hero = function (opts) {
    opts = opts || {};
    var el = BV.el("div", { class: "hero" + (opts.stick ? " stick" : "") + (opts.class ? " " + opts.class : "") });
    var html = '<span class="robot-name">' + BV.esc(opts.name) + "</span>";
    if (opts.model) html += '<span class="robot-model">' + BV.esc(opts.model) + "</span>";
    var sub = (opts.sub || []).filter(Boolean);
    if (sub.length) {
      html += '<span class="hero-sub">' +
        sub.map(BV.esc).join('<span class="sep"> · </span>') + "</span>";
    }
    el.innerHTML = html;
    if (opts.chips) {
      if (typeof opts.chips === "string") el.insertAdjacentHTML("beforeend", opts.chips);
      else el.appendChild(opts.chips);
    }
    return el;
  };
})();
