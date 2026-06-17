/* tabs/home.js - the ecosystem main menu (#home). Renders without a backup
   open: three action tiles (open / add-to-library / take-a-backup) plus the
   saved robot library grouped PLANT -> LINE -> ROBOT. Marked shell:true so the
   router lets it render with no manifest. */
(function () {
  "use strict";

  var _libWrap = null;  /* the mounted library container, for in-place refresh */

  function actionTile(opts) {
    var c = BV.card({ title: opts.title, class: "home-tile" });
    c.el.appendChild(BV.el("div", { class: "home-tile-desc" }, BV.esc(opts.desc)));
    c.el.setAttribute("role", "button");
    c.el.tabIndex = 0;
    c.el.addEventListener("click", opts.onClick);
    c.el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onClick(); }
    });
    return c.el;
  }

  function render(view, toolbar, params) {
    view.appendChild(BV.hero({
      name: "FANUC Backup", model: "ecosystem",
      sub: ["view · library · take backups"], class: "home-hero",
    }));

    var tiles = BV.el("div", { class: "cards home-actions" });
    tiles.appendChild(actionTile({
      title: "open backup", desc: "browse a backup folder on disk",
      onClick: function () { BV.openBackupFlow(); },
    }));
    tiles.appendChild(actionTile({
      title: "add to library", desc: "save a backup folder to your library",
      onClick: function () { BV.addToLibraryFlow(); },
    }));
    tiles.appendChild(actionTile({
      title: "take a new backup", desc: "pull a fresh backup from a robot over the network",
      onClick: function () { location.hash = "#backup"; },
    }));
    view.appendChild(tiles);

    _libWrap = BV.el("div", { class: "home-library" });
    view.appendChild(_libWrap);
    loadLibrary();
  }

  function loadLibrary() {
    if (!_libWrap) return;
    _libWrap.innerHTML =
      '<div class="home-lib-head"><h2>library</h2>' +
      '<button class="btn" id="lib-add-manual" title="add a robot manually">+ add robot</button></div>' +
      '<div class="home-lib-body"><div class="dim">loading…</div></div>';
    _libWrap.querySelector("#lib-add-manual").addEventListener("click", function () {
      editRobotModal(null, true);
    });
    BV.api.call("lib_list").then(function (data) {
      renderTree(_libWrap.querySelector(".home-lib-body"), data);
    }).catch(function (e) {
      _libWrap.querySelector(".home-lib-body").innerHTML =
        '<div class="dim">library unavailable: ' + BV.esc(e.message) + "</div>";
    });
  }

  function refresh() {
    if (_libWrap && document.body.contains(_libWrap)) loadLibrary();
  }

  function renderTree(body, data) {
    var robots = (data && data.robots) || [];
    if (!robots.length) {
      body.innerHTML = '<div class="empty-lib">no robots saved yet — add a backup, or take one.</div>';
      return;
    }
    var plants = {};
    robots.forEach(function (r) {
      var pl = r.plant || "—", ln = r.line || "—";
      plants[pl] = plants[pl] || {};
      plants[pl][ln] = plants[pl][ln] || [];
      plants[pl][ln].push(r);
    });
    body.innerHTML = "";
    Object.keys(plants).sort().forEach(function (pl) {
      var plantEl = BV.el("div", { class: "lib-plant" });
      plantEl.appendChild(BV.el("div", { class: "lib-plant-h" }, BV.esc(pl)));
      var lines = plants[pl];
      Object.keys(lines).sort().forEach(function (ln) {
        var lineEl = BV.el("div", { class: "lib-line" });
        lineEl.appendChild(BV.el("div", { class: "lib-line-h" }, BV.esc(ln)));
        lines[ln].sort(function (a, b) {
          return (a.robot || "").localeCompare(b.robot || "");
        }).forEach(function (r) { lineEl.appendChild(robotRow(r)); });
        plantEl.appendChild(lineEl);
      });
      body.appendChild(plantEl);
    });
  }

  function robotRow(r) {
    var row = BV.el("div", { class: "lib-robot" + (r.stale ? " stale" : "") });

    var main = BV.el("div", { class: "lib-robot-main" });
    var nameHtml = '<span class="lib-robot-name">' + BV.esc(r.robot || "(unnamed)") + "</span>";
    if (r.model) nameHtml += '<span class="lib-robot-model">' + BV.esc(r.model) + "</span>";
    main.appendChild(BV.el("div", null, nameHtml));
    var meta = [];
    if (r.ips && r.ips.length) meta.push(BV.esc(r.ips[0]));
    if (r.last_backup) meta.push("last " + BV.esc(r.last_backup));
    if (r.backups && r.backups.length) meta.push(r.backups.length + " saved");
    if (r.stale) meta.push('<span class="pill warn">missing</span>');
    main.appendChild(BV.el("div", { class: "lib-robot-meta" },
      meta.join(' <span class="sep">·</span> ')));
    row.appendChild(main);

    var acts = BV.el("div", { class: "lib-robot-acts" });
    var openBtn = BV.el("button", { class: "btn" }, "open");
    openBtn.addEventListener("click", function (e) { e.stopPropagation(); openRobot(r); });
    var editBtn = BV.el("button", { class: "btn", title: "edit" }, "edit");
    editBtn.addEventListener("click", function (e) { e.stopPropagation(); editRobotModal(r, false); });
    var rmBtn = BV.el("button", { class: "btn", title: "remove from library" }, "✕");
    rmBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      BV.api.call("lib_remove", r.id).then(function () { BV.toast("removed"); refresh(); })
        .catch(function (err) { BV.toast(err.message); });
    });
    acts.appendChild(openBtn);
    acts.appendChild(editBtn);
    acts.appendChild(rmBtn);
    row.appendChild(acts);

    row.addEventListener("click", function () { openRobot(r); });
    return row;
  }

  function openRobot(r) {
    if (r.stale) { BV.toast("backup folder missing on disk"); return; }
    BV.api.call("lib_open", r.id, "latest").then(function (manifest) {
      BV.state.setManifest(manifest);
      BV.toast(manifest.robot_name
        ? manifest.robot_name + " · " + manifest.file_count + " files" : "opened");
      location.hash = "#overview";
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* ---- add / edit modal ---- */

  function inp(value, attrs) {
    var a = { type: "text", value: value || "", spellcheck: "false", class: "lf-input" };
    if (attrs) Object.keys(attrs).forEach(function (k) { a[k] = attrs[k]; });
    return BV.el("input", a);
  }
  function field(label, el) {
    var row = BV.el("div", { class: "lf-row" });
    row.appendChild(BV.el("label", null, BV.esc(label)));
    row.appendChild(el);
    return row;
  }

  function editRobotModal(entry, isNew) {
    entry = entry || {};
    var form = BV.el("div", { class: "lib-form" });

    var fPlant = inp(entry.plant);
    var fLine = inp(entry.line);
    var fRobot = inp(entry.robot || entry.robot_name);
    var fModel = inp(entry.model);
    var fIps = inp((entry.ips || []).join(", "));
    var fPath = inp(entry.latest_path, entry.latest_path ? { readonly: "readonly" } : null);
    var fUser = inp((entry.ftp || {}).user);
    var fPassive = BV.el("input", { type: "checkbox", class: "lf-check" });
    if (!entry.ftp || entry.ftp.passive !== false) fPassive.checked = true;
    var fNotes = inp(entry.notes);

    form.appendChild(field("plant", fPlant));
    form.appendChild(field("line", fLine));
    form.appendChild(field("robot", fRobot));
    form.appendChild(field("model", fModel));
    form.appendChild(field("ip(s)", fIps));
    form.appendChild(field("folder", fPath));
    form.appendChild(field("ftp user", fUser));
    form.appendChild(field("passive ftp", fPassive));
    form.appendChild(field("notes", fNotes));

    var actions = BV.el("div", { class: "lf-actions" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var save = BV.el("button", { class: "btn primary" }, isNew ? "add" : "save");
    actions.appendChild(cancel);
    actions.appendChild(save);
    form.appendChild(actions);

    var m = BV.modal(isNew ? "add robot" : "edit robot", form);
    cancel.addEventListener("click", m.close);
    fRobot.focus();

    save.addEventListener("click", function () {
      var robot = fRobot.value.trim();
      if (!robot) { BV.toast("robot name required"); return; }
      var fields = {
        plant: fPlant.value.trim(), line: fLine.value.trim(),
        robot: robot, model: fModel.value.trim(),
        ips: fIps.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean),
        latest_path: fPath.value.trim(), notes: fNotes.value.trim(),
        ftp: { user: fUser.value.trim(), passive: fPassive.checked },
      };
      var p;
      if (isNew) {
        var draft = {
          f_number: entry.f_number || "", backup_type: entry.backup_type || "",
        };
        Object.keys(fields).forEach(function (k) { draft[k] = fields[k]; });
        p = BV.api.call("lib_add", draft);
      } else {
        p = BV.api.call("lib_update", entry.id, fields);
      }
      p.then(function () { m.close(); BV.toast(isNew ? "added" : "saved"); refresh(); })
        .catch(function (e) { BV.toast(e.message); });
    });
  }

  /* picked-folder -> draft -> add modal */
  BV.addToLibraryFlow = function () {
    BV.api.call("pick_backup_folder").then(function (path) {
      if (!path) return null;
      return BV.api.call("lib_scan_folder", path).then(function (draft) {
        editRobotModal(draft, true);
      });
    }).catch(function (e) { BV.toast(e.message); });
  };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "home", label: "home", render: render, hidden: true, always: true, shell: true });
})();
