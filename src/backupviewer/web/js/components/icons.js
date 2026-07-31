/* icons.js - the app's tiny stroke-icon set (BV.icon).
   One source of truth for every svg glyph that is not one of the three
   topbar cubes, in the cubes' language: 24-box, stroke 1.7, round caps and
   joins, currentColor. Emoji are banned here - they ignore CSS color, so
   they can never follow the theme (the old 📱/⚙ never matched anything).
   Geometry was tuned by eye in .claude/sandbox/icons.html (untracked);
   phone + gear are frozen output of that page's generators. */
(function () {
  "use strict";

  var PATHS = {
    /* bent handset at 42deg, cups up: the handle is a band of a bend
       circle, the cup lobes sit radially on it so they lean inward */
    phone: '<g transform="rotate(42 12 12)"><path d="M5.82 7.1 L9.38 9.15 L8.72 11.31 ' +
      'A11.3 11.3 0 0 0 15.28 11.31 L14.62 9.15 L18.18 7.1 L21.57 10.72 ' +
      'A14 14 0 0 1 2.43 10.72 Z"/></g>',
    /* 8 broad teeth + a real hub - few chunky teeth is what keeps this
       reading "gear" instead of "ship's wheel" at 1em */
    gear: '<path d="M10.14 4.53 L10.51 2.01 A10.1 10.1 0 0 1 13.49 2.01 L13.86 4.53 ' +
      'A7.7 7.7 0 0 1 15.97 5.4 L18.01 3.88 A10.1 10.1 0 0 1 20.12 5.99 L18.6 8.03 ' +
      'A7.7 7.7 0 0 1 19.47 10.14 L21.99 10.51 A10.1 10.1 0 0 1 21.99 13.49 L19.47 13.86 ' +
      'A7.7 7.7 0 0 1 18.6 15.97 L20.12 18.01 A10.1 10.1 0 0 1 18.01 20.12 L15.97 18.6 ' +
      'A7.7 7.7 0 0 1 13.86 19.47 L13.49 21.99 A10.1 10.1 0 0 1 10.51 21.99 L10.14 19.47 ' +
      'A7.7 7.7 0 0 1 8.03 18.6 L5.99 20.12 A10.1 10.1 0 0 1 3.88 18.01 L5.4 15.97 ' +
      'A7.7 7.7 0 0 1 4.53 13.86 L2.01 13.49 A10.1 10.1 0 0 1 2.01 10.51 L4.53 10.14 ' +
      'A7.7 7.7 0 0 1 5.4 8.03 L3.88 5.99 A10.1 10.1 0 0 1 5.99 3.88 L8.03 5.4 ' +
      'A7.7 7.7 0 0 1 10.14 4.53 Z"/><circle cx="12" cy="12" r="3.5"/>',
    help: '<path d="M9.35 8.9 C9.35 6.95 10.55 5.8 12.15 5.8 C13.75 5.8 14.85 6.9 14.85 8.4 ' +
      'C14.85 9.9 13.9 10.55 12.95 11.2 C12.15 11.75 11.95 12.3 11.95 13.5 L11.95 13.9"/>' +
      '<path d="M11.95 17.3 L11.95 17.35"/>',
    /* padlock pair for the MultiTable scroll link (closed = panes together) */
    lock: '<rect x="5.5" y="10.5" width="13" height="9" rx="1.6"/>' +
      '<path d="M8.5 10.5 L8.5 7.5 A3.5 3.5 0 0 1 15.5 7.5 L15.5 10.5"/>',
    unlock: '<rect x="5.5" y="10.5" width="13" height="9" rx="1.6"/>' +
      '<path d="M8.5 10.5 L8.5 7.5 A3.5 3.5 0 0 1 15.5 7.5 L15.5 8.9"/>',
    /* monitor + filled cursor: "drive that screen from here" */
    remote: '<rect x="3.2" y="4.8" width="17.6" height="12.4" rx="1.6"/>' +
      '<path d="M12 17.2 L12 19.6 M8.4 19.6 L15.6 19.6"/>' +
      '<path d="M9.8 6.4 L9.8 13.3 L11.5 11.6 L12.7 14 L14.1 13.3 L12.9 11 L15.2 10.8 Z" ' +
      'fill="currentColor" stroke="none"/>'
  };

  BV.icon = function (name) {
    return '<svg class="bv-ico bv-ico-' + name + '" viewBox="0 0 24 24" aria-hidden="true" ' +
      'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
      'stroke-linejoin="round">' + PATHS[name] + "</svg>";
  };

  /* static holders (the topbar trio) declare data-icon in index.html */
  [].forEach.call(document.querySelectorAll("[data-icon]"), function (el) {
    el.innerHTML = BV.icon(el.getAttribute("data-icon"));
  });
})();
