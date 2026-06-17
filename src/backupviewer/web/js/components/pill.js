/* components/pill.js - the little rounded status badge used all over the app
   (signal on/off, dcs ok/changed, alarm facility, macro assign type, ...).

   One constructor, reused everywhere. The CHOICE of variant and any "no value
   -> no pill" rule stay at the call site, because those encode meaning that
   differs per tab (io shows nothing for a blank state, alarms shows a dash).

   variant is one of the .pill modifier classes from components.css:
     "" (neutral) | on | off | err | warn | acc | ghost | ok-soft */
(function () {
  "use strict";

  BV.pill = function (text, variant) {
    return '<span class="pill' + (variant ? " " + variant : "") + '">' +
      BV.esc(text) + "</span>";
  };

  /* same thing, but a live DOM node instead of an HTML string */
  BV.pill.node = function (text, variant) {
    return BV.el("span", { class: "pill" + (variant ? " " + variant : "") }, BV.esc(text));
  };

  /* convenience for the common "map a status string to a variant" case;
     displays the text as-is. e.g. BV.pill.map(st, {ok:"ok-soft",chgd:"warn"}, "err") */
  BV.pill.map = function (text, table, dflt) {
    var v = (table && Object.prototype.hasOwnProperty.call(table, text)) ? table[text] : dflt;
    return BV.pill(text, v);
  };
})();
