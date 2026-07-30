/* tabs/files.js - raw file browser with text / hex preview, plus extract:
   tick files, pick a folder (a USB stick, a share), and byte-for-byte copies
   land there - the files-tab sibling of the editor's export, same endpoints
   for the folder pick and the reveal, same never-into-a-backup contract. */
(function () {
  "use strict";

  var vt = null;

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    if (params && params[0]) renderFile(view, toolbar, decodeURIComponent(params[0]));
    else renderList(view, toolbar);
  }

  function robotLabel() {
    var m = BV.state.manifest || {};
    return m.robot_name || m.name || "";
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
      var rb = null;
      if ((m.device_type || "").indexOf("camera") === 0 && m.camera_ip) {
        var isCvx = m.device_type === "camera-keyence";
        rb = BV.el("button", { class: "btn", style: "margin-left:auto",
          title: (isCvx ? "mirror this camera's live screen ("
                        : "open this camera's web UI (") + BV.esc(m.camera_ip) + ")" },
          "🖥 remote");
        rb.addEventListener("click", function () {
          if (isCvx) BV.openCvxRemote(m.camera_ip, m.camera_name);
          else BV.openMtxRemote(m.camera_ip, m.camera_name);
        });
        toolbar.appendChild(rb);
      }

      /* --- tick files, extract byte-for-byte copies. Same shape as the
         programs tab's workspace picks: VTable has no multi-select, so the
         tick set lives here (in tabState, per backup), the column re-reads
         it every paint, and the click is caught in the CAPTURE phase before
         the row's own handler opens the file. Shift extends from the last
         tick; the header box is "every file this filter shows". */
      var picked = fst.picked || (fst.picked = {});
      var lastPick = null;
      var headCb = null;
      function pickedRows() {
        return files.filter(function (f) { return picked[f.rel]; });
      }
      function clearPicks() {
        Object.keys(picked).forEach(function (k) { delete picked[k]; });
        lastPick = null;
      }
      function viewRows() { return (vt && vt.view) || []; }
      function allPicked() {
        var v = viewRows();
        return !!(v.length && v.every(function (r) { return picked[r.rel]; }));
      }
      function syncExtractBtn() {
        var n = pickedRows().length;
        xBtn.textContent = n ? "⇪ extract (" + n + ")" : "⇪ extract";
        xBtn.classList.toggle("primary", !!n);
        xBtn.disabled = !n;
        if (headCb) headCb.checked = allPicked();
      }
      function setAll(on) {
        viewRows().forEach(function (r) {
          if (on) picked[r.rel] = true; else delete picked[r.rel];
        });
        lastPick = null;
        if (vt) vt.repaint();
        syncExtractBtn();
      }
      function onPickClick(ev) {
        var cb = ev.target;
        if (!cb || !cb.classList || !cb.classList.contains("vt-pick")) return;
        ev.stopPropagation();                 /* never open the file */
        var rel = cb.getAttribute("data-k");
        var v = viewRows();
        var idx = -1, prev = -1;
        v.forEach(function (r, i) {
          if (r.rel === rel) idx = i;
          if (lastPick && r.rel === lastPick) prev = i;
        });
        if (ev.shiftKey && prev >= 0 && idx >= 0) {
          var lo = Math.min(prev, idx), hi = Math.max(prev, idx);
          for (var i = lo; i <= hi; i++) {
            var r2 = v[i];
            if (!r2 || !r2.rel) continue;
            if (cb.checked) picked[r2.rel] = true; else delete picked[r2.rel];
          }
        } else if (cb.checked) picked[rel] = true;
        else delete picked[rel];
        lastPick = rel;
        if (vt) vt.repaint();
        syncExtractBtn();
      }

      var xBtn = BV.el("button", { class: "btn",
        style: rb ? "" : "margin-left:auto",
        title: "copy the ticked files, byte-for-byte, to a folder you choose " +
          "(a USB stick, a share) — never into a backup" }, "⇪ extract");
      xBtn.disabled = true;
      xBtn.addEventListener("click", function () {
        var sel = pickedRows();
        if (sel.length) showExtract(sel, afterExtract);
      });
      toolbar.appendChild(xBtn);
      function afterExtract() {
        clearPicks();
        if (vt) vt.repaint();
        syncExtractBtn();
      }

      var host = BV.el("div", { style: "height:100%;margin:0 1.25rem 1rem" });
      view.appendChild(host);
      host.addEventListener("click", onPickClick, true);

      if (vt) vt.destroy();
      vt = new BV.VTable(host, {
        columns: [
          { key: "_pick", label: "", width: 34, sortable: false, resizable: false,
            headRender: function () {
              headCb = BV.el("input", { type: "checkbox", class: "vt-pickall",
                title: "select every file this filter shows" });
              headCb.addEventListener("click", function (ev) {
                ev.stopPropagation();
                setAll(headCb.checked);
              });
              return headCb;
            },
            render: function (r) {
              return '<input type="checkbox" class="vt-pick" data-k="' +
                BV.esc(r.rel) + '"' + (picked[r.rel] ? " checked" : "") + ">";
            } },
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
        onCount: function (n) { sb.setCount(n, files.length); syncExtractBtn(); },
        onOpen: function (r) { location.hash = "#files/" + encodeURIComponent(r.rel); },
      });
      BV.currentVTable = vt;
      vt.setFilter(sb.value());
      syncExtractBtn();
    }).catch(function (e) {
      view.classList.remove("no-pad");
      view.innerHTML = '<div class="empty-state"><div class="big">files unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  /* ------------------------------------------------------------------ *
   * extract - mirror of the editor's export flow (edit.js showExport):
   * confirm what will land where, pick_export_folder, then the guarded
   * copy, then a done-modal with reveal-in-explorer.
   * ------------------------------------------------------------------ */
  function showExtract(sel, onDone) {
    var label = robotLabel();
    var total = 0;
    sel.forEach(function (f) { total += f.size || 0; });
    var body = BV.el("div");
    var html = '<div class="dim" style="font-size:.8rem;margin-bottom:.4rem">' +
      "copies are byte-for-byte — nothing is converted, renamed, or removed " +
      "from the backup</div>" +
      '<div style="margin:.5rem 0 .2rem;font-family:var(--font-mono);color:var(--accent)">' +
      BV.esc(label || "backup") + "/</div>" +
      '<div style="max-height:40vh;overflow:auto">';
    sel.forEach(function (f) {
      html += '<div style="padding-left:1.2rem;font-family:var(--font-mono);font-size:.82rem">' +
        BV.esc(f.rel) + "</div>";
    });
    html += "</div>" +
      '<div class="dim" style="font-size:.8rem;margin-top:.4rem">' +
      sel.length + " file" + (sel.length === 1 ? "" : "s") + " · " +
      BV.esc(BV.fmt.bytes(total)) + "</div>";
    body.innerHTML = html;
    var acts = BV.el("div",
      { style: "display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var go = BV.el("button", { class: "btn primary" }, "choose folder…");
    acts.appendChild(cancel);
    acts.appendChild(go);
    body.appendChild(acts);
    var m = BV.modal("extract files", body);
    cancel.addEventListener("click", function () { m.close(true); });
    go.addEventListener("click", function () {
      m.close(true);
      BV.api.call("pick_export_folder").then(function (dest) {
        if (!dest) return;
        var rels = sel.map(function (f) { return f.rel; });
        return BV.api.call("files_extract", rels, dest, label).then(function (res) {
          if (onDone) onDone();
          showExtracted(res);
        });
      }).catch(function (e) { BV.toast("extract failed: " + (e.message || e), 4500); });
    });
  }

  function showExtracted(res) {
    var body = BV.el("div");
    body.innerHTML = "<p>extracted <b>" + res.count + "</b> file" +
      (res.count === 1 ? "" : "s") + " · " + BV.esc(BV.fmt.bytes(res.bytes || 0)) +
      " to</p>" +
      '<p style="font-family:var(--font-mono);font-size:.8rem;word-break:break-all">' +
      BV.esc(res.dest) + "</p>" +
      '<ul class="dim" style="margin:.3rem 0 0 1.1rem;font-size:.82rem;max-height:40vh;overflow:auto">' +
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
