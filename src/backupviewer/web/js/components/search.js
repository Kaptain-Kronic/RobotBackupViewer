/* search.js - debounced filter input bound to a vtable (or any onChange). '/' focuses it. */
(function () {
  "use strict";

  BV.searchBox = function (opts) {
    var wrap = BV.el("div", { class: "search-box" });
    wrap.appendChild(BV.el("span", { class: "si" }, "/"));
    var input = BV.el("input", {
      type: "text",
      placeholder: opts.placeholder || "filter…",
      spellcheck: "false",
    });
    wrap.appendChild(input);
    /* a click anywhere on the box (icon, padding) must focus the input -
       otherwise typing silently goes nowhere */
    wrap.addEventListener("click", function (e) {
      if (e.target !== input) input.focus();
    });
    var count = BV.el("span", { class: "match-count" });
    wrap.appendChild(count);

    var fire = BV.debounce(function () {
      opts.onChange(input.value.trim());
    }, opts.delay === undefined ? 150 : opts.delay);

    input.addEventListener("input", fire);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (input.value) {
          input.value = "";
          opts.onChange("");
        } else {
          input.blur();
        }
        e.stopPropagation();
      } else if (e.key === "Enter" || e.key === "ArrowDown") {
        input.blur();
        if (opts.onCommit) opts.onCommit();
        e.preventDefault();
        e.stopPropagation();
      }
    });

    return {
      el: wrap,
      input: input,
      focus: function () { input.focus(); input.select(); },
      setCount: function (n, total) {
        count.textContent = (total !== undefined && n !== total) ? n + "/" + total : (n === undefined ? "" : String(n));
      },
      value: function () { return input.value.trim(); },
    };
  };
})();
