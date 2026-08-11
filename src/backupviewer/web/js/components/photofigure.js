/* components/photofigure.js - the photo hero figure: one image in a framed
   box, or a CV-X crossfade pair. A CV-X stores every scene TWICE - a
   grayscale photo and a height map of the same moment - so the pair stacks
   both in one object-fit box and an opacity slider mixes them. Extracted
   from tabs/photos.js so every screen that shows the figure shares one
   implementation.

   opts:
     photo  - the photo record; when it carries .overlay {base, top,
              base_label, top_label} the crossfade pair renders and drives
              itself, otherwise a single image driven by node.show(rel)
     load   - (rel) => Promise resolving to a data-uri string OR {data_uri}
              (both loader shapes exist; the component takes either)
     state  - persistence object; the component reads/writes state.mix
              (slider %, default 100) so the mix survives photo switches
              and tab returns
     onOpen - click handler, called with a rel string (single image) or
              {base, top, mix} (pair); null/undefined = not clickable

   Returns the DOM node (a flex column: figure, plus the slider row for a
   pair) with one method attached: node.show(rel) loads/switches the
   displayed image on the single-image path - the caller picks the initial
   rel and any later switch (e.g. a filtered/raw toggle). A failed height
   layer disables the slider and SAYS "height unavailable" - a live slider
   quietly crossfading to nothing would read as "flat part", a claim. */
(function () {
  "use strict";

  BV.photoFigure = function (opts) {
    var p = opts.photo || {};
    var st = opts.state || {};
    function load(rel) {
      /* accept a loader resolving to a bare data-uri string (photos.js's
         LRU'd loadImage) or the raw api shape {data_uri} */
      return Promise.resolve(opts.load(rel)).then(function (r) {
        return r && typeof r === "object" ? r.data_uri : r;
      });
    }

    var figCol = BV.el("div", { style:
      "flex:1 1 23.75rem;min-width:17.5rem;max-width:40rem;display:flex;" +
      "flex-direction:column;gap:0.45rem" });
    var figure = BV.el("div", { style:
      "background:var(--bg2);border:1px solid var(--sub-alt);border-radius:8px;" +
      "overflow:hidden;display:flex;align-items:center;justify-content:center;" +
      "min-height:13.75rem" });
    if (opts.onOpen) {
      figure.style.cursor = "zoom-in";
      figure.title = "click to view fullscreen";
    }
    var img = BV.el("img", { alt: BV.esc(p.name), style:
      "max-width:100%;max-height:46vh;display:block;object-fit:contain" });
    var curRel = "";
    function showImage(rel) {
      if (!rel) return;
      curRel = rel;
      load(rel).then(function (uri) { img.src = uri; }).catch(function () {
        figure.innerHTML = '<span class="dim">image unavailable</span>';
      });
    }

    var ov = p.overlay;
    if (ov) {
      /* the height layer is absolutely positioned over the base and both
         use the same object-fit box, which keeps them registered at any
         size; the pan/zoom-free stack is what fullscreen re-creates */
      var stack = BV.el("div", { style: "position:relative;line-height:0;max-width:100%" });
      stack.appendChild(img);
      var top = BV.el("img", { alt: "", style:
        "position:absolute;inset:0;width:100%;height:100%;object-fit:contain;" +
        "pointer-events:none" });
      stack.appendChild(top);
      figure.appendChild(stack);

      var mix = st.mix === undefined ? 100 : Number(st.mix);
      top.style.opacity = String(mix / 100);
      var slider = BV.el("input", { type: "range", min: "0", max: "100",
        step: "1", value: String(mix), "aria-label": "height / greyscale mix" });
      var readout = BV.el("span", { class: "range-val" }, mix + "%");
      slider.addEventListener("input", function (e) {
        var v = Number(e.target.value);
        st.mix = v;                       /* sticky across photos and tab returns */
        top.style.opacity = String(v / 100);
        readout.textContent = v + "%";
      });

      showImage(ov.base);
      load(ov.top).then(function (uri) { top.src = uri; }).catch(function () {
        /* a missing height half must SAY so - a live slider quietly
           crossfading to nothing would read as "flat part", a claim */
        top.style.opacity = "0";
        slider.disabled = true;
        readout.textContent = "height unavailable";
      });

      var row = BV.el("div", { style:
        "display:flex;align-items:center;gap:0.5rem;font-size:0.78rem;color:var(--sub)" });
      row.appendChild(BV.el("span", {}, BV.esc(ov.base_label || "photo")));
      var sliderWrap = BV.el("div", { class: "range-wrap" });
      sliderWrap.appendChild(slider);
      sliderWrap.appendChild(readout);   /* .range-val is styled inside .range-wrap */
      row.appendChild(sliderWrap);
      row.appendChild(BV.el("span", {}, BV.esc(ov.top_label || "overlay")));
      figCol.appendChild(figure);
      figCol.appendChild(row);
      if (opts.onOpen) figure.addEventListener("click", function () {
        opts.onOpen({ base: ov.base, top: ov.top,
          mix: Number(st.mix === undefined ? 100 : st.mix) });
      });
    } else {
      figure.appendChild(img);
      figCol.appendChild(figure);
      if (opts.onOpen) figure.addEventListener("click", function () {
        if (curRel) opts.onOpen(curRel);
      });
    }

    figCol.show = showImage;
    return figCol;
  };
})();
