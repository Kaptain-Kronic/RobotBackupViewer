/* highlight_tp.js - regex-based syntax highlighting for FANUC TP program lines.
   Input: one raw body line (already separated from its line number).
   Output: HTML string with tp-* span classes (theme-colored in components.css). */
(function () {
  "use strict";

  /* token patterns, applied in order on the escaped line */
  var RULES = [
    /* strings in quotes */
    { re: /('[^']*'|"[^"]*")/g, cls: "tp-str" },
    /* IO ports: DO[71:Process1TaskOk]  DI[5]  UO/UI/GI/GO/RI/RO/SI/SO/WI/WO/AI/AO/F[..]  */
    { re: /\b(DO|DI|UO|UI|GO|GI|RO|RI|SO|SI|WO|WI|AO|AI|F|M)\[[^\]]*\]/g, cls: "tp-io" },
    /* registers: R[151:...] PR[1,2] AR[1] SR[3] VR[..] PL[..] */
    { re: /\b(PR|R|AR|SR|VR|PL|DR)\[[^\]]*\]/g, cls: "tp-reg" },
    /* labels */
    { re: /\bLBL\[[^\]]*\]/g, cls: "tp-label" },
    /* position refs P[12] / P[3:name] */
    { re: /\bP\[[^\]]*\]/g, cls: "tp-reg" },
    /* system variables $FOO.$BAR */
    { re: /\$[A-Z_][A-Z0-9_.$\[\]]*/g, cls: "tp-sysvar" },
    /* keywords */
    {
      re: /\b(IF|THEN|ELSE|ENDIF|JMP|CALL|WAIT|SELECT|CASE|TIMEOUT|ABORT|PAUSE|RUN|MESSAGE|UFRAME_NUM|UTOOL_NUM|UFRAME|UTOOL|OVERRIDE|PAYLOAD|RSR|END|FOR|ENDFOR|SKIP|OFFSET|TOOL_OFFSET|MONITOR|ENDMON|AND|OR|NOT|ON|OFF|DIV|MOD|TC_ONLINE|POINT_LOGIC|SPOT)\b/g,
      cls: "tp-kw",
    },
    /* motion speed/term: 100% 2000mm/sec FINE CNT100 */
    { re: /\b(FINE|CNT\d*|\d+%|max_speed|\d+(?:\.\d+)?(?:mm\/sec|cm\/min|sec|deg\/sec|inch\/min|msec))\b/g, cls: "tp-num" },
  ];

  function tokenize(line) {
    /* comment lines: everything after ! is comment */
    var esc = BV.esc(line);
    var bang = line.indexOf("!");
    if (bang >= 0 && /^\s*!/.test(line)) {
      return '<span class="tp-comment">' + esc + "</span>";
    }

    /* protect ranges so later rules don't re-match inside earlier spans */
    var marks = []; /* {start, end, cls} on the escaped string */

    RULES.forEach(function (rule) {
      rule.re.lastIndex = 0;
      var m;
      while ((m = rule.re.exec(esc)) !== null) {
        var s = m.index, e = m.index + m[0].length;
        var clash = marks.some(function (k) { return s < k.end && e > k.start; });
        if (!clash) marks.push({ start: s, end: e, cls: rule.cls });
        if (m.index === rule.re.lastIndex) rule.re.lastIndex++;
      }
    });

    /* leading motion instruction: "J P[1] ..." / "L ..." / "C ..." / "A ..." */
    var mm = /^(\s*)([JLCA])(\s)/.exec(esc);
    if (mm) {
      var s2 = mm[1].length, e2 = s2 + 1;
      var clash2 = marks.some(function (k) { return s2 < k.end && e2 > k.start; });
      if (!clash2) marks.push({ start: s2, end: e2, cls: "tp-motion" });
    }

    if (!marks.length) return esc;
    marks.sort(function (a, b) { return a.start - b.start; });
    var out = "", pos = 0;
    marks.forEach(function (k) {
      out += esc.slice(pos, k.start);
      out += '<span class="' + k.cls + '">' + esc.slice(k.start, k.end) + "</span>";
      pos = k.end;
    });
    out += esc.slice(pos);
    return out;
  }

  BV.highlightTP = tokenize;
})();
