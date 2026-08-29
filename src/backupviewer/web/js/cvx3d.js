/* cvx3d.js - BV.cvx3d: the camera 3D screen behind the "3d view" tab.

   A Keyence CV-X backup carries the camera's own 3D model containers - part
   CAD, workspace scans, gripper CAD, matching templates, a robot model and
   the hand-eye calibration records. This screen lists ALL of them (evidence),
   draws the ones whose geometry is proven (BV.meshView - canvas 2D, no WebGL,
   no libraries), and extracts any drawn stream as a binary STL through
   cvx_model_export. Non-viewable containers get an info card instead - listed
   honestly with what IS known, never a guessed mesh.

   Reached from tabs/view3d.js: a camera backup branches here (cameras carry
   scanner CAD, not DCS); robot backups never touch this file. Load order:
   util.js, api.js, components/meshview.js before this file (index.html). */
(function () {
  "use strict";

  /* kind -> rail group, in display order; unknown kinds land in "other" */
  var GROUPS = [
    ["part", "parts"],
    ["scan", "workspace scans"],
    ["hand", "hand"],
    ["robot", "robot"],
    ["calibration", "calibration"],
    ["template", "templates"],
  ];

  var MESH_CACHE_MAX = 8;   /* decoded meshes are big - keep a few, refetch the rest */

  function fmtTris(n) {
    return (n === null || n === undefined) ? "" : Number(n).toLocaleString("en-US");
  }

  /* mirror of the server's filename rule (api.cvx_model_export): program, tool
     and kind joined by underscores, the stream number appended past stream 0 */
  function stlName(e) {
    var stem = [e.program, e.tool, e.kind].filter(Boolean).join("_");
    if (!stem) {
      var leaf = (e.rel || "").split("/").pop() || "model";
      stem = leaf.replace(/\.[^.]*$/, "");
    }
    if (e.stream) stem += "_" + e.stream;
    return stem + ".stl";
  }

  function exportLabel() {
    var man = BV.state.manifest || {};
    return man.camera_name || man.robot_name || man.name || "camera";
  }

  /* ---- extract flow ----
     The edit tab's export/exported modal composition, kept local: no shared
     export-modal primitive exists yet (edit.js holds the only other copy) -
     promote both into one when a third caller appears. */

  function showExtract(items) {
    if (!items.length) { BV.toast("nothing to extract"); return; }
    var body = BV.el("div");
    var html = '<div class="dim" style="font-size:.8rem;margin-bottom:.4rem">' +
      "geometry decoded from the camera's own files — unmodified</div>" +
      '<div style="margin:.5rem 0 .2rem;font-family:var(--font-mono);color:var(--accent)">' +
      BV.esc(exportLabel()) + "/</div>";
    items.forEach(function (e) {
      html += '<div style="padding-left:1.2rem;font-family:var(--font-mono);font-size:.82rem">' +
        BV.esc(stlName(e)) + "</div>";
    });
    body.innerHTML = html;
    var acts = BV.el("div",
      { style: "display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var go = BV.el("button", { class: "btn primary" }, "choose folder…");
    acts.appendChild(cancel);
    acts.appendChild(go);
    body.appendChild(acts);
    var m = BV.modal("extract stl", body);
    cancel.addEventListener("click", function () { m.close(true); });
    go.addEventListener("click", function () {
      m.close(true);
      BV.api.call("pick_export_folder").then(function (dest) {
        if (!dest) return;   /* dialog cancelled - silent */
        return BV.api.call("cvx_model_export",
          items.map(function (e) { return { rel: e.rel, stream: e.stream }; }),
          dest, exportLabel()).then(showExtracted);
      }).catch(function (e) { BV.toast("extract failed: " + (e.message || e), 4500); });
    });
  }

  function showExtracted(res) {
    var body = BV.el("div");
    body.innerHTML = "<p>extracted <b>" + res.count + "</b> file" +
      (res.count === 1 ? "" : "s") + " (" + BV.esc(BV.fmt.bytes(res.bytes)) + ") to</p>" +
      '<p style="font-family:var(--font-mono);font-size:.8rem;word-break:break-all">' +
      BV.esc(res.dest) + "</p>" +
      '<ul class="dim" style="margin:.3rem 0 0 1.1rem;font-size:.82rem">' +
      (res.files || []).map(function (f) { return "<li>" + BV.esc(f) + "</li>"; }).join("") +
      "</ul>";
    var acts = BV.el("div",
      { style: "display:flex;gap:.6rem;justify-content:flex-end;margin-top:1rem" });
    var reveal = BV.el("button", { class: "btn" }, "reveal in explorer");
    var done = BV.el("button", { class: "btn primary" }, "done");
    acts.appendChild(reveal);
    acts.appendChild(done);
    body.appendChild(acts);
    var m = BV.modal("extracted", body);
    reveal.addEventListener("click", function () {
      BV.api.call("reveal_export_folder", res.dest).catch(function () {});
    });
    done.addEventListener("click", function () { m.close(true); });
  }

  /* ---- the screen ---- */

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    BV.api.call("cvx_models").then(function (data) {
      var models = (data && data.models) || [];
      if (!models.length) {
        /* unreachable through the tab gate (no models = the tab never lights);
           an honest fallback for a hand-typed hash */
        view.innerHTML = '<div class="empty-state"><div class="big">no 3d models</div>' +
          '<div class="hint">this backup carries no decodable model containers</div></div>';
        return;
      }
      /* same tab id as the robot 3d view - a backup is a camera OR a robot,
         so the two states can never collide in one bucket */
      var s = BV.tabState("view3d");
      view.classList.add("v3-host");   /* the flex row the robot 3d view uses */

      function keyOf(e) { return e.rel + BV.KEYSEP + e.stream; }
      var byKey = {};
      models.forEach(function (e) { byKey[keyOf(e)] = e; });

      /* left rail (own scroll) + main pane: header row over the canvas/info stage */
      var rail = BV.el("aside", { class: "cvx3d-rail", style:
        "flex:0 0 18rem;max-width:45%;min-height:0;overflow-y:auto;padding:.25rem .1rem 1rem" });
      var main = BV.el("div", { class: "cvx3d-main", style:
        "flex:1 1 auto;min-width:0;min-height:0;display:flex;flex-direction:column;gap:.5rem" });
      var head = BV.el("div", { class: "cvx3d-head", style:
        "flex:none;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;min-height:1.9rem" });
      var stage = BV.el("div", { style: "flex:1 1 auto;min-height:0;position:relative" });
      /* the frame is chrome like the robot viewport's (.v3-svg): var(--edge),
         so the borders-off setting flattens both the same way */
      var canvasHost = BV.el("div", { style:
        "position:absolute;inset:0;background:var(--bg2);border:1px solid var(--edge);" +
        "border-radius:8px;overflow:hidden" });
      var infoHost = BV.el("div", { style: "position:absolute;inset:0;overflow:auto;display:none" });
      stage.appendChild(canvasHost);
      stage.appendChild(infoHost);
      main.appendChild(head);
      main.appendChild(stage);
      view.appendChild(rail);
      view.appendChild(main);

      /* the orientation cube rides the stage top-right - the same primitive
         the robot 3d view carries (components/viewcube.js). Created after
         the mesh view so the shared state carries its seeded az/el; the
         onDraw guard covers the construction gap. It hides with the canvas
         when an info card takes the stage. */
      var cube = null;
      var mv = BV.meshView(canvasHost, { state: s,
        onDraw: function () { if (cube) cube.update(); } });
      cube = BV.viewCube(stage, {
        basisOf: function () { return BV.proj3d.orbitProjector(s.az, s.el).basis; },
        onSnap: function (az, el) {
          s.az = az;
          s.el = el;
          s.zoom = null;
          mv.redraw();
        },
      });
      var meshCache = new Map();   /* keyOf -> decoded mesh payload, capped LRU */
      var cur = null;              /* the loaded mesh for the current selection */

      /* toolbar: extract every decoded stream (byte-identical copies folded out) */
      var allItems = models.filter(function (e) { return e.viewable && !e.dup_of; });
      if (allItems.length) {
        var allBtn = BV.el("button", { class: "btn",
          title: "write every decoded model as an .stl file (identical copies skipped)" },
          "extract all (" + allItems.length + ")");
        allBtn.addEventListener("click", function () { showExtract(allItems); });
        toolbar.appendChild(allBtn);
      }

      var rows = {};   /* keyOf -> rail row el (dup rows keyed by their OWN key) */

      function markSelected(e) {
        var want = keyOf(e);
        Object.keys(rows).forEach(function (k) {
          var on = k === want;
          rows[k].style.background = on ? "var(--bg2)" : "";
          rows[k].style.boxShadow = on ? "inset 2px 0 0 var(--accent)" : "";
        });
      }

      /* name + a sub slot; the mesh path appends its buttons after this */
      function buildHead(e) {
        head.innerHTML = "";
        head.appendChild(BV.el("span", { style:
          "font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;" +
          "white-space:nowrap" }, BV.esc(e.name)));
        var sub = BV.el("span", { class: "dim", style: "font-size:.82rem" });
        head.appendChild(sub);
        return sub;
      }

      /* non-viewable containers: an info card instead of the mesh - what the
         file IS, never a guessed shape */
      function showInfo(e) {
        canvasHost.style.display = "none";
        cube.el.style.display = "none";   /* no 3D view -> no orientation instrument */
        infoHost.style.display = "";
        infoHost.innerHTML = "";
        buildHead(e).textContent = e.kind;
        var pairs = [
          ["label", e.label],
          ["kind", e.kind],
          ["file size", BV.fmt.bytes(e.size)],
        ];
        if (e.kind === "robot") {
          pairs.push(["maker", e.maker]);
          pairs.push(["model", e.model]);
        }
        if (e.kind === "calibration" && e.points !== undefined) {
          pairs.push(["recorded point pairs", String(e.points)]);
        }
        var note = "";
        if (e.kind === "hand") note = "geometry encoding not decoded — listed as evidence";
        else if (e.kind === "template") note = "matching templates derived from the part model";
        var co = BV.card({ title: e.name });
        co.el.style.maxWidth = "30rem";
        co.el.appendChild(BV.kv(pairs));
        if (note) co.el.insertAdjacentHTML("beforeend",
          '<div class="dim" style="font-size:.82rem;margin-top:.4rem">' + BV.esc(note) + "</div>");
        infoHost.appendChild(co.el);
      }

      function showMesh(e) {
        infoHost.style.display = "none";
        canvasHost.style.display = "";
        cube.el.style.display = "";
        var sub = buildHead(e);
        sub.textContent = "loading…";
        var btns = BV.el("span", { style: "margin-left:auto;display:flex;gap:.4rem" });
        var fitBtn = BV.el("button", { class: "btn",
          title: "reset pan/zoom (double-click the canvas does too)" }, "fit");
        var perspBtn = BV.el("button", {
          class: "btn" + (s.persp ? " primary" : ""),
          title: "perspective projection — off = orthographic (parallel, true to scale; the mm ruler is orthographic-only)",
        }, "perspective");
        var bigBtn = BV.el("button", { class: "btn",
          title: "enlarge (shares the same orientation)", disabled: "" }, "enlarge");
        var extBtn = BV.el("button", { class: "btn",
          title: "write this model as an .stl file" }, "extract stl");
        btns.appendChild(fitBtn);
        btns.appendChild(perspBtn);
        btns.appendChild(bigBtn);
        btns.appendChild(extBtn);
        head.appendChild(btns);
        fitBtn.addEventListener("click", function () { s.zoom = null; mv.redraw(); });
        perspBtn.addEventListener("click", function () {
          s.persp = !s.persp;
          perspBtn.classList.toggle("primary", s.persp);
          mv.redraw();
        });
        bigBtn.addEventListener("click", function () {
          if (!cur) return;
          BV.meshView.enlarge(cur, { state: s, onClose: function () { mv.redraw(); } });
        });
        extBtn.addEventListener("click", function () { showExtract([e]); });

        var k = keyOf(e);
        var cached = meshCache.get(k);
        var p = cached ? Promise.resolve(cached)
          : BV.api.call("cvx_model", e.rel, e.stream);
        p.then(function (mesh) {
          if (!s.sel || keyOf(s.sel) !== k) return;   /* stale - another row picked */
          meshCache.delete(k);
          meshCache.set(k, mesh);
          if (meshCache.size > MESH_CACHE_MAX) meshCache.delete(meshCache.keys().next().value);
          cur = mesh;
          bigBtn.disabled = false;
          mv.setMesh(mesh);
          sub.innerHTML = BV.esc(fmtTris(mesh.tri_count) + " triangles") +
            (mesh.decimated
              ? ' <span style="opacity:.8">· decimated view — export keeps full detail</span>'
              : "");
        }).catch(function (err) {
          sub.textContent = "model unavailable";
          BV.toast(err.message || String(err));
        });
      }

      function select(e) {
        var master = e.dup_of
          ? (byKey[e.dup_of.rel + BV.KEYSEP + e.dup_of.stream] || e) : e;
        s.sel = { rel: master.rel, stream: master.stream };
        cur = null;
        markSelected(master);
        if (master.viewable) showMesh(master);
        else showInfo(master);
      }

      function railRow(e) {
        var r = BV.el("div", { class: "cvx3d-row", style:
          "padding:.35rem .5rem;border-radius:6px;cursor:pointer;min-width:0" });
        var line = BV.el("div", { style: "display:flex;align-items:center;gap:.4rem;min-width:0" });
        line.insertAdjacentHTML("beforeend",
          '<span style="flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;' +
          'white-space:nowrap">' + BV.esc(e.name) + "</span>");
        if (e.viewable) {
          line.insertAdjacentHTML("beforeend", BV.pill(fmtTris(e.tris) + " tris", "ghost"));
        } else {
          line.insertAdjacentHTML("beforeend",
            '<span class="dim" style="flex:none;font-size:.72rem">' +
            BV.esc(BV.fmt.bytes(e.size)) + "</span>");
        }
        r.appendChild(line);
        if (e.dup_of) {
          var master = byKey[e.dup_of.rel + BV.KEYSEP + e.dup_of.stream];
          r.insertAdjacentHTML("beforeend",
            '<div class="dim" style="font-size:.72rem">same as ' +
            BV.esc(master ? master.name : e.dup_of.rel) + "</div>");
        }
        r.addEventListener("click", function () { select(e); });
        rows[keyOf(e)] = r;
        return r;
      }

      var grouped = {};
      models.forEach(function (e) { (grouped[e.kind] = grouped[e.kind] || []).push(e); });
      var known = {};
      GROUPS.forEach(function (g) { known[g[0]] = 1; });
      var lists = GROUPS.map(function (g) { return [g[1], grouped[g[0]] || []]; });
      lists.push(["other", models.filter(function (e) { return !known[e.kind]; })]);

      /* the rail leads with what the viewer can actually draw; families whose
         encodings are not decoded (hand/robot/calibration/templates today)
         fold behind an explicit toggle - hidden by default, never dropped.
         When a family's decode lands its entries turn viewable and move up
         on their own. If NOTHING is drawable the undecoded list starts open
         (an all-hidden rail would claim the backup holds no models). */
      function groupHead(gl) {
        return BV.el("div", { style:
          "margin:.7rem 0 .15rem;padding:0 .5rem;font-size:.72rem;color:var(--sub);" +
          "letter-spacing:.04em" },
          BV.esc(gl[0]) + ' <span style="opacity:.7">' + gl[1].length + "</span>");
      }
      /* The rail leads with the PART models - the registered part CAD is what
         this screen is for, and a technician opening it is looking for a part.
         Everything else the backup carries (workspace scans, the gripper, the
         arm, calibration, templates, layouts) folds behind one toggle: still
         here, still openable, just not in the way. */
      var drawable = [], undecoded = [];
      lists.forEach(function (gl) {
        if (!gl[1].length) return;   /* empty groups vanish */
        (gl[1].some(function (e) { return e.kind === "part"; })
          ? drawable : undecoded).push(gl);
      });
      drawable.forEach(function (gl) {
        rail.appendChild(groupHead(gl));
        gl[1].forEach(function (e) { rail.appendChild(railRow(e)); });
      });
      var rawCount = undecoded.reduce(function (n, gl) { return n + gl[1].length; }, 0);
      var repaintRaw = function () {};
      if (rawCount) {
        if (s.showRaw === undefined) s.showRaw = !drawable.length;
        var rawHost = BV.el("div");
        var toggle = BV.el("button", { class: "btn", style:
          "margin:.9rem .5rem .2rem;font-size:.78rem" });
        function paintRaw() {
          toggle.textContent = (s.showRaw ? "hide" : "show") +
            " other models & data (" + rawCount + ")";
          rawHost.style.display = s.showRaw ? "" : "none";
        }
        toggle.addEventListener("click", function () {
          s.showRaw = !s.showRaw;
          paintRaw();
        });
        undecoded.forEach(function (gl) {
          rawHost.appendChild(groupHead(gl));
          gl[1].forEach(function (e) { rawHost.appendChild(railRow(e)); });
        });
        rail.appendChild(toggle);
        rail.appendChild(rawHost);
        paintRaw();
        repaintRaw = paintRaw;
      }
      BV.persistScroll("view3d-rail", rail);

      /* restore the last selection when it still exists; else first drawable */
      var init = s.sel && byKey[s.sel.rel + BV.KEYSEP + s.sel.stream];
      if (!init) {
        init = models.find(function (e) { return e.viewable && !e.dup_of; }) || models[0];
      }
      /* a restored selection may live in the folded undecoded list - unfold
         so the highlighted row is never invisible */
      if (init && !init.viewable && !s.showRaw) {
        s.showRaw = true;
        repaintRaw();
      }
      select(init);

      var onResize = BV.debounce(function () {
        if (!document.contains(view)) {
          window.removeEventListener("resize", onResize);
          return;
        }
        mv.resize();
      }, 150);
      window.addEventListener("resize", onResize);
    }).catch(function (e) {
      view.classList.remove("v3-host");
      view.innerHTML = '<div class="empty-state"><div class="big">3d models unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.cvx3d = { render: render };
})();
