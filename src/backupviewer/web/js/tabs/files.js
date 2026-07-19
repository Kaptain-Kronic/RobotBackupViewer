/* tabs/files.js - raw file browser with text / hex preview */
(function () {
  "use strict";

  var vt = null;

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    if (params && params[0]) renderFile(view, toolbar, decodeURIComponent(params[0]));
    else renderList(view, toolbar);
  }

  function renderList(view, toolbar) {
    view.classList.add("no-pad");
    BV.api.call("list_files").then(function (files) {
      var fst = BV.tabState("files");
      var typeFilter = fst.typeFilter || null;
      var extSet = {};
      files.forEach(function (f) { if (f.ext) extSet[f.ext] = 1; });
      var exts = Object.keys(extSet).sort();
      if (typeFilter && exts.indexOf(typeFilter) < 0) typeFilter = fst.typeFilter = null;
      function typedFiles() {
        return typeFilter ? files.filter(function (f) { return f.ext === typeFilter; }) : files;
      }

      var sb = BV.searchBox({
        placeholder: "filter files…",
        onChange: function (q) { if (vt) vt.setFilter(q); },
        onCommit: function () { if (vt) vt.moveSelection(1); },
      });
      toolbar.appendChild(sb.el);
      BV.currentSearch = sb;

      /* file-type filter (by extension) - sits next to the search box */
      if (exts.length > 1) {
        var typeSeg = BV.segmented(
          [{ id: "all", label: "all" }].concat(exts.map(function (e) {
            return { id: e, label: e.toLowerCase() };
          })),
          { value: typeFilter || "all", onChange: function (id) {
              typeFilter = fst.typeFilter = (id === "all" ? null : id);
              if (vt) { vt.setData(typedFiles()); vt.setFilter(sb.value()); }
            } }
        );
        toolbar.appendChild(typeSeg.el);
      }

      /* a camera backup gets a live remote button: CV-X mirrors the controller's
         screen + mouse (cvxremote.js); Matrox embeds the camera's own web UI
         (mtxremote.js) */
      var m = BV.state.manifest || {};
      if ((m.device_type || "").indexOf("camera") === 0 && m.camera_ip) {
        var isCvx = m.device_type === "camera-keyence";
        var rb = BV.el("button", { class: "btn", style: "margin-left:auto",
          title: (isCvx ? "mirror this camera's live screen ("
                        : "open this camera's web UI (") + BV.esc(m.camera_ip) + ")" },
          "🖥 remote");
        rb.addEventListener("click", function () {
          if (isCvx) BV.openCvxRemote(m.camera_ip, m.camera_name);
          else BV.openMtxRemote(m.camera_ip, m.camera_name);
        });
        toolbar.appendChild(rb);
      }

      var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
      view.appendChild(host);

      if (vt) vt.destroy();
      vt = new BV.VTable(host, {
        columns: [
          { key: "rel", label: "file", width: 320, accent: true, render: function (r) {
              if (r.rel === r.name) return BV.esc(r.name);
              var dir = r.rel.slice(0, r.rel.length - r.name.length);
              return '<span class="dim">' + BV.esc(dir) + "</span>" + BV.esc(r.name);
            } },
          { key: "ext", label: "type", width: 80, dim: true },
          { key: "binary", label: "", width: 80, render: function (r) {
              return r.binary ? '<span class="pill ghost">bin</span>' : "";
            } },
          { key: "size", label: "size", width: 110, num: true, render: function (r) {
              return BV.fmt.bytes(r.size);
            } },
          { key: "mtime", label: "modified", grow: true, dim: true, render: function (r) {
              return BV.esc(BV.fmt.epoch(r.mtime));
            } },
        ],
        data: typedFiles(),
        stateKey: "files",   /* persist scroll + sort across nav (in-session) */
        onCount: function (n) { sb.setCount(n, files.length); },
        onOpen: function (r) { location.hash = "#files/" + encodeURIComponent(r.rel); },
      });
      BV.currentVTable = vt;
      vt.setFilter(sb.value());
    }).catch(function (e) {
      view.classList.remove("no-pad");
      view.innerHTML = '<div class="empty-state"><div class="big">files unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  function renderFile(view, toolbar, name) {
    view.classList.remove("no-pad");
    BV.api.call("get_file", name).then(function (f) {
      var crumb = BV.el("div", { class: "crumb" });
      crumb.innerHTML = '<span class="back" id="fcrumb-hist" title="previous view (backspace)">← back</span>' +
        '<span class="back" id="fcrumb-list">files</span>' +
        '<span class="title">' + BV.esc(f.name) + "</span>" +
        '<span class="dim">' +
        (f.rel && f.rel !== f.name ? BV.esc(f.rel) + " · " : "") +
        BV.fmt.bytes(f.size) +
        (f.kind === "hex" ? " · binary (hex preview)" : "") +
        (f.truncated ? " · truncated" : "") + "</span>";
      crumb.querySelector("#fcrumb-hist").addEventListener("click", function () { history.back(); });
      crumb.querySelector("#fcrumb-list").addEventListener("click", function () {
        location.hash = "#files";
      });
      view.appendChild(crumb);

      var viewer = BV.el("div", { class: "viewer", style: "height:calc(100% - 2.4rem)" });
      var pre = BV.el("pre", { style: "padding:0 1rem" });
      pre.textContent = f.text;
      viewer.appendChild(pre);
      view.appendChild(viewer);
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">cannot open file</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "files", label: "files", render: render });
})();
