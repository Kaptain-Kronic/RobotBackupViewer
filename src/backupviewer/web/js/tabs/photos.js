/* tabs/photos.js - Matrox camera saved-image viewer. Shows the most recent
   inspection photo big (with its parsed pass/fail report) over a lazy-loading
   thumbnail grid of the rest. Images come over the bridge as base64 data-URIs;
   the grid loads each thumb only as it scrolls into view.

   The renderer is exposed as BV.renderPhotos(view, toolbar, source) so a robot's
   Cameras tab reuses it against a linked camera's backup (source supplies the
   photos() + image(rel) data calls). */
(function () {
  "use strict";

  function resultVariant(result) {
    var r = (result || "").toLowerCase();
    if (r === "pass") return "on";
    if (r === "fail") return "err";
    return "ghost";
  }

  /* source: { photos:()=>Promise<data>, image:(rel)=>Promise<{data_uri}>,
               stateKey?:string } */
  BV.renderPhotos = function (view, toolbar, source) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    view.classList.add("no-pad");
    if (source.leading) toolbar.appendChild(source.leading);   /* e.g. a back button */

    var imgCache = {};   /* rel -> data_uri, per render (avoids cross-camera mixups) */
    function loadImage(rel) {
      if (imgCache[rel]) return Promise.resolve(imgCache[rel]);
      return source.image(rel).then(function (im) {
        imgCache[rel] = im.data_uri;
        return im.data_uri;
      });
    }

    function openFullscreen(rel) {
      var overlay = BV.el("div", { style:
        "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.93);cursor:zoom-out;" +
        "display:flex;align-items:center;justify-content:center" });
      var fimg = BV.el("img", { alt: "", style: "max-width:98vw;max-height:98vh;object-fit:contain" });
      overlay.appendChild(fimg);
      loadImage(rel).then(function (uri) { fimg.src = uri; }).catch(function () {
        overlay.innerHTML = '<span class="dim">image unavailable</span>';
      });
      function close() { document.removeEventListener("keydown", onKey); overlay.remove(); }
      function onKey(e) { if (e.key === "Escape") close(); }
      overlay.addEventListener("click", close);
      document.addEventListener("keydown", onKey);
      document.body.appendChild(overlay);
    }

    source.photos().then(function (data) {
      var photos = data.photos || [];
      if (!photos.length) {
        view.classList.remove("no-pad");
        view.innerHTML = '<div class="empty-state"><div class="big">no photos</div>' +
          '<div class="hint">this backup has no saved inspection images</div></div>';
        return;
      }

      var pst = BV.tabState(source.stateKey || "photos");
      var filter = pst.filter || "all";

      function pass(p) { return (p.result || "").toLowerCase() === "pass"; }
      function fail(p) { return (p.result || "").toLowerCase() === "fail"; }
      function filtered() {
        if (filter === "pass") return photos.filter(pass);
        if (filter === "fail") return photos.filter(fail);
        return photos;
      }

      /* toolbar: pass/fail filter + camera label */
      var nPass = photos.filter(pass).length;
      var nFail = photos.filter(fail).length;
      var seg = BV.segmented([
        { id: "all", label: "all " + photos.length },
        { id: "pass", label: "pass " + nPass },
        { id: "fail", label: "fail " + nFail },
      ], { value: filter, onChange: function (id) {
          filter = pst.filter = id;
          var list = filtered();
          selectPhoto(list[0] || null);
          buildGrid(list);
        } });
      toolbar.appendChild(seg.el);
      var cam = data.camera || {};
      if (cam.name || cam.type) {
        var camLabel = BV.el("span", { class: "dim", style: "margin-left:auto;align-self:center" },
          BV.esc([cam.name, cam.type, cam.ip].filter(Boolean).join(" · ")));
        toolbar.appendChild(camLabel);
      }

      /* layout: hero over grid */
      var wrap = BV.el("div", { style: "height:100%;overflow:auto;padding:0 1.25rem 1rem" });
      var hero = BV.el("div", { id: "photo-hero", style:
        "display:flex;gap:1.25rem;flex-wrap:wrap;align-items:flex-start;margin:0.75rem 0 1rem" });
      var grid = BV.el("div", { id: "photo-grid", style:
        "display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:0.6rem" });
      wrap.appendChild(hero);
      wrap.appendChild(grid);
      view.appendChild(wrap);

      var selected = null;

      function selectPhoto(p) {
        selected = p;
        hero.innerHTML = "";
        if (!p) return;

        /* left: the full image */
        var figure = BV.el("div", { style:
          "flex:1 1 380px;min-width:280px;max-width:640px;background:var(--bg2);" +
          "border:1px solid var(--sub-alt);border-radius:8px;overflow:hidden;" +
          "display:flex;align-items:center;justify-content:center;min-height:220px" });
        figure.style.cursor = "zoom-in";
        var img = BV.el("img", { alt: BV.esc(p.name), style:
          "max-width:100%;max-height:46vh;display:block;object-fit:contain" });
        figure.appendChild(img);
        var curRel = "";
        function showImage(rel) {
          if (!rel) return;
          curRel = rel;
          loadImage(rel).then(function (uri) { img.src = uri; }).catch(function () {
            figure.innerHTML = '<span class="dim">image unavailable</span>';
          });
        }
        showImage(p.thumb || p.full);   /* default to the annotated (green-boxes) jpg */
        figure.addEventListener("click", function () { if (curRel) openFullscreen(curRel); });
        hero.appendChild(figure);

        /* right: the parsed inspection report */
        var panel = BV.el("div", { style: "flex:1 1 300px;min-width:260px" });
        var badge = BV.pill((p.result || "unknown").toUpperCase(), resultVariant(p.result));
        panel.insertAdjacentHTML("beforeend",
          '<div style="font-size:1.15rem;margin-bottom:0.4rem">' + badge +
          ' <span class="dim" style="font-size:0.85rem">' + BV.esc(p.timestamp || p.date) +
          "</span></div>");

        panel.insertAdjacentHTML("beforeend", BV.kv.html([
          ["recipe", p.recipe && p.recipe.name],
          ["recipe id", p.recipe && p.recipe.id],
          ["exposure", p.recipe && p.recipe.exposure],
          ["camera", p.camera && p.camera.name],
          ["model", p.camera && p.camera.type],
          ["software", p.camera && p.camera.software],
          ["ip", p.camera && p.camera.ip],
          ["project", p.camera && p.camera.project],
        ]));

        if (p.tools && p.tools.length) {
          var tools = p.tools.map(function (t) {
            return BV.pill(t.name + ": " + t.result, resultVariant(t.result));
          }).join(" ");
          panel.insertAdjacentHTML("beforeend",
            '<div style="margin-top:0.6rem;display:flex;gap:0.35rem;flex-wrap:wrap">' + tools + "</div>");
        }

        /* image controls: green boxes (annotated jpg) vs raw (clean png), + fullscreen */
        var ctrls = BV.el("div", { style:
          "margin-top:0.7rem;display:flex;gap:0.6rem;align-items:center;flex-wrap:wrap" });
        if (p.thumb && p.full && p.thumb !== p.full) {
          var viewSeg = BV.segmented([
            { id: "boxes", label: "green boxes" },
            { id: "raw", label: "raw" },
          ], { value: "boxes", onChange: function (id) {
              showImage(id === "raw" ? p.full : p.thumb);
            } });
          ctrls.appendChild(viewSeg.el);
        }
        var fsBtn = BV.el("button", { class: "btn" }, "fullscreen");
        fsBtn.addEventListener("click", function () { if (curRel) openFullscreen(curRel); });
        ctrls.appendChild(fsBtn);
        panel.appendChild(ctrls);

        /* full report sections, collapsed */
        if (p.sections && p.sections.length) {
          var card = BV.card({ title: "full report", collapsible: true, startCollapsed: true });
          p.sections.forEach(function (sec) {
            if (!sec.rows || !sec.rows.length) return;
            card.body.insertAdjacentHTML("beforeend",
              '<h4 style="margin:0.5rem 0 0.15rem;color:var(--sub)">' + BV.esc(sec.title) + "</h4>");
            card.body.insertAdjacentHTML("beforeend", BV.kv.html(
              sec.rows.map(function (r) { return [r.key, r.value]; })));
          });
          card.head.style.cursor = "pointer";
          card.head.addEventListener("click", function () {
            card.el.classList.toggle("collapsed");
          });
          panel.appendChild(card.el);
        }
        hero.appendChild(panel);
      }

      /* lazy-loading thumbnail grid */
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var cell = en.target;
          observer.unobserve(cell);
          var rel = cell.dataset.rel;
          loadImage(rel).then(function (uri) {
            var im = cell.querySelector("img");
            if (im) im.src = uri;
          }).catch(function () {});
        });
      }, { root: wrap, rootMargin: "300px" });

      function buildGrid(list) {
        grid.innerHTML = "";
        list.forEach(function (p) {
          var v = resultVariant(p.result);
          var border = v === "err" ? "var(--error)" : (v === "on" ? "var(--ok)" : "var(--sub-alt)");
          var cell = BV.el("div", { style:
            "cursor:pointer;border:2px solid " + border + ";border-radius:6px;overflow:hidden;" +
            "background:var(--bg2)" });
          cell.dataset.rel = p.thumb;
          var box = BV.el("div", { style:
            "height:104px;display:flex;align-items:center;justify-content:center;background:var(--bg)" });
          var im = BV.el("img", { alt: "", loading: "lazy", style:
            "max-width:100%;max-height:104px;object-fit:contain;display:block" });
          box.appendChild(im);
          cell.appendChild(box);
          var cap = BV.el("div", { style:
            "font-size:0.68rem;padding:0.2rem 0.35rem;color:var(--sub);white-space:nowrap;" +
            "overflow:hidden;text-overflow:ellipsis" },
            BV.esc((p.result ? p.result + " · " : "") + shortTime(p)));
          cell.appendChild(cap);
          cell.addEventListener("click", function () {
            selectPhoto(p);
            wrap.scrollTo({ top: 0, behavior: "smooth" });
          });
          grid.appendChild(cell);
          observer.observe(cell);
        });
      }

      function shortTime(p) {
        var m = /(\d{2})\.(\d{2})\.(\d{2})\.\d+\.[a-z]+$/i.exec(p.name);
        var t = m ? m[1] + ":" + m[2] + ":" + m[3] : "";
        return [p.date, t].filter(Boolean).join(" ");
      }

      var list0 = filtered();
      selectPhoto(list0[0] || null);
      buildGrid(list0);
    }).catch(function (e) {
      view.classList.remove("no-pad");
      view.innerHTML = '<div class="empty-state"><div class="big">photos unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  };

  /* the single "photos" tab covers two cases:
     - a camera backup (or a folder with SavedImages): its own photos
     - a robot opened from the library with linked cameras: a grid of those
       cameras, click one for its full photos view (same renderer) */
  function render(view, toolbar, params) {
    var m = BV.state.manifest || {};
    if (m.robot_id && m.cameras_count) {
      if (params && params[0]) renderOneCamera(view, toolbar, decodeURIComponent(params[0]));
      else renderCameraList(view, toolbar, m.robot_id);
      return;
    }
    BV.renderPhotos(view, toolbar, {
      photos: function () { return BV.api.call("get_photos"); },
      image: function (rel) { return BV.api.call("get_image", rel); },
    });
  }

  function camImage(camId, rel) { return BV.api.call("get_camera_image", camId, rel); }

  function renderCameraList(view, toolbar, robotId) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    view.classList.remove("no-pad");
    BV.api.call("lib_robot_cameras", robotId).then(function (data) {
      var cams = data.cameras || [];
      if (!cams.length) {
        view.innerHTML = '<div class="empty-state"><div class="big">no linked cameras</div>' +
          '<div class="hint">link cameras to this robot from the home library</div></div>';
        return;
      }
      var grid = BV.el("div", { style:
        "display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));" +
        "gap:1rem;padding:1rem 1.25rem" });
      cams.forEach(function (c) {
        var kind = c.device_type === "camera-keyence" ? "CV-X" : "MTX";
        var badge = c.result ? " " + BV.pill(c.result.toUpperCase(), resultVariant(c.result)) : "";
        var card = BV.el("div", { style:
          "cursor:pointer;border:1px solid var(--sub-alt);border-radius:8px;" +
          "overflow:hidden;background:var(--bg2)" });
        var box = BV.el("div", { style:
          "height:150px;display:flex;align-items:center;justify-content:center;background:var(--bg)" });
        if (!c.thumb) {
          box.innerHTML = '<span class="dim">' + (c.has_backup ? "no photo" : "no backup yet") + "</span>";
        } else {
          var im = BV.el("img", { alt: "", style:
            "max-width:100%;max-height:150px;object-fit:contain;display:block" });
          box.appendChild(im);
          camImage(c.id, c.thumb).then(function (r) { im.src = r.data_uri; }).catch(function () {});
        }
        card.appendChild(box);
        card.insertAdjacentHTML("beforeend",
          '<div style="padding:0.5rem 0.6rem">' +
          '<div class="lib-robot-name">' + BV.esc(c.name) + "</div>" +
          '<div class="lib-robot-meta">' + BV.pill(kind, "acc") + badge +
          (c.photos ? ' <span class="dim">' + c.photos + " photos</span>" : "") +
          (c.last_backup ? ' <span class="dim">· ' + BV.esc(c.last_backup) + "</span>" : "") +
          "</div></div>");
        if (c.ips && c.ips[0]) {
          var isCvx = c.device_type === "camera-keyence";
          var rb = BV.el("button", { class: "btn", style:
            "margin:0 0.6rem 0.6rem;padding:0.18rem 0.5rem;font-size:0.76rem",
            title: (isCvx ? "mirror this camera's live screen ("
                          : "open this camera's web UI (") + BV.esc(c.ips[0]) + ")" }, "🖥 remote");
          rb.addEventListener("click", function (e) {
            e.stopPropagation();
            if (isCvx) BV.openCvxRemote(c.ips[0], c.name);
            else BV.openMtxRemote(c.ips[0], c.name);
          });
          card.appendChild(rb);
        }
        card.addEventListener("click", function () {
          location.hash = "#photos/" + encodeURIComponent(c.id);
        });
        grid.appendChild(card);
      });
      view.appendChild(grid);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">cameras unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  function renderOneCamera(view, toolbar, camId) {
    var back = BV.el("span", { class: "back", style: "cursor:pointer;align-self:center" }, "← cameras");
    back.addEventListener("click", function () { location.hash = "#photos"; });
    BV.renderPhotos(view, toolbar, {
      leading: back,
      photos: function () { return BV.api.call("get_camera_photos", camId); },
      image: function (rel) { return camImage(camId, rel); },
      stateKey: "camera:" + camId,
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "photos", label: "photos", render: render });
})();
