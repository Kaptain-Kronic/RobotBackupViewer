/* sim_export.js - "load cameras…": copy chosen CV-X workspaces into the flat
   folder the simulator's base path points at.

   Why a picker and not an automatic mirror: each camera workspace runs ~100 MB,
   and the simulator only ever needs the handful you are actually about to open.
   So nothing is copied until you ask, and asking overwrites that camera's
   previous copy in place.

   Only cameras whose LATEST backup actually holds a workspace on disk can appear
   (the Python side reads the folders, not the library's claims), so a camera
   backed up before the workspace layout is honestly absent with a reason shown
   rather than silently missing. */
(function () {
  "use strict";

  function fmtBytes(n) {
    if (!n) return "";
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
    return (i ? n.toFixed(1) : String(n)) + " " + u[i];
  }

  /* onClose() - run when the picker goes away, however it went (loaded, esc,
     backdrop). There is ONE modal root, so opening this picker tears down the
     settings dialog it was launched from; the caller passes a reopen here so you
     land back where you pressed the button instead of at the library. */
  BV.simExport = function (onClose) {
    var opts = { onClose: onClose };
    BV.api.call("sim_candidates").then(function (r) {
      var cams = (r && r.cameras) || [];
      var root = (r && r.root) || "";
      var body = BV.el("div", { class: "sim-export" });

      body.appendChild(BV.el("p", { class: "sim-dest" },
        "copied into " + BV.esc(root)));

      if (!cams.length) {
        body.appendChild(BV.el("p", { class: "dim" },
          "no camera backups hold a simulator workspace yet. take a Keyence " +
          "backup — every CV-X pull is a workspace from now on."));
        BV.modal("load cameras into the simulator", body, opts);
        return;
      }

      var cl = BV.checklist({ onChange: sync });
      var rows = BV.el("div", { class: "sim-rows" });

      var selAll = BV.el("label", { class: "sim-selall", title: "select all / clear" });
      selAll.appendChild(cl.group(BV.el("input", { type: "checkbox", class: "lf-check" }),
        function () { return cams.map(function (c) { return c.key; }); }, "all"));
      selAll.appendChild(BV.el("span", null, "all"));
      rows.appendChild(selAll);

      cams.forEach(function (c) {
        var lab = BV.el("label", { class: "sim-row" });
        lab.appendChild(cl.bind(BV.el("input", { type: "checkbox", class: "lf-check" }), c.key));

        var main = BV.el("span", { class: "sim-main" });
        main.appendChild(BV.el("span", { class: "sim-name" }, BV.esc(c.name)));
        /* "already there" is two different things and must never read the same:
           our own earlier export is routine, a folder we did not create is
           somebody's hand-made simulator save that loading would destroy. */
        if (c.foreign) main.appendChild(BV.pill.node("not ours", "err"));
        else if (c.already) main.appendChild(BV.pill.node("replaces", "warn"));
        lab.appendChild(main);

        var sub = [];
        if (c.station && c.station !== c.name) sub.push(c.station);
        if (c.line) sub.push(c.line);
        if (c.ip) sub.push(c.ip);
        if (c.files) sub.push(c.files + " files · " + fmtBytes(c.bytes));
        lab.appendChild(BV.el("span", { class: "sim-sub dim" }, BV.esc(sub.join(" · "))));

        /* say WHY a folder name had to be qualified - a silently renamed
           workspace is the kind of surprise that costs a trip to the cell */
        if (c.why) lab.appendChild(BV.el("span", { class: "sim-why dim" }, BV.esc(c.why)));
        if (c.foreign) {
          lab.appendChild(BV.el("span", { class: "sim-why err" },
            "a folder called " + BV.esc(c.name) + " is already there and " +
            "BackupViewer did not create it — loading would replace it"));
        }
        rows.appendChild(lab);
      });
      body.appendChild(rows);

      var foot = BV.el("div", { class: "sim-foot" });
      var count = BV.el("span", { class: "dim" }, "");
      var go = BV.el("button", { class: "btn primary" }, "load");
      foot.appendChild(count);
      foot.appendChild(go);
      body.appendChild(foot);

      var modal = BV.modal("load cameras into the simulator", body, opts);

      function sync() {
        var keys = cl.selected();
        var bytes = 0;
        cams.forEach(function (c) { if (keys.indexOf(c.key) >= 0) bytes += c.bytes || 0; });
        count.textContent = keys.length
          ? keys.length + " selected · " + fmtBytes(bytes)
          : "nothing selected";
        go.disabled = !keys.length;
      }
      sync();

      go.addEventListener("click", function () { run(cl.selected(), false); });

      /* replaceForeign is only ever true after confirmForeign() below got a
         deliberate yes - never as a retry-on-error shortcut. */
      function run(keys, replaceForeign) {
        if (!keys.length) return;
        go.disabled = true;
        go.textContent = "copying…";
        BV.api.call("sim_export", keys, !!replaceForeign).then(function (res) {
          var blocked = res.blocked || [];
          if (blocked.length) {
            go.disabled = false;
            go.textContent = "load";
            confirmForeign(blocked, res, keys);
            return;
          }
          modal.close(true);          /* onClose reopens whatever launched us */
          var n = (res.exported || []).length, bad = (res.failed || []).length;
          BV.toast(bad ? (n + " loaded, " + bad + " failed")
                       : (n + " loaded — point the simulator at " + res.root), 4000);
        }).catch(function (e) {
          go.disabled = false;
          go.textContent = "load";
          BV.toast(e.message);
        });
      }

      /* The one destructive moment in this feature: say plainly what would be
         destroyed, name every folder, and default to the safe way out. */
      function confirmForeign(blocked, res, keys) {
        var b = BV.el("div", { class: "sim-export" });
        var n = (res.exported || []).length;
        b.appendChild(BV.el("p", null,
          (n ? n + " loaded. " : "") + "<b>" + blocked.length + "</b> " +
          (blocked.length === 1 ? "camera was" : "cameras were") + " not loaded: a folder " +
          "of that name is already in the simulator folder and BackupViewer did not " +
          "create it. Replacing it deletes whatever is in it — including a workspace " +
          "you built by hand."));
        var list = BV.el("div", { class: "sim-rows" });
        blocked.forEach(function (x) {
          var r = BV.el("div", { class: "sim-row" });
          r.appendChild(BV.el("span", null, ""));
          r.appendChild(BV.el("span", { class: "sim-name" }, BV.esc(x.name)));
          r.appendChild(BV.el("span", { class: "sim-sub dim" }, BV.esc(x.path)));
          list.appendChild(r);
        });
        b.appendChild(list);

        var foot = BV.el("div", { class: "sim-foot" });
        var keep = BV.el("button", { class: "btn" }, "keep what's there");
        var wipe = BV.el("button", { class: "btn danger" }, "replace them");
        foot.appendChild(keep);
        foot.appendChild(wipe);
        b.appendChild(foot);

        /* opening this modal takes over the one root, so the picker behind it is
           already gone; closing THIS one runs opts.onClose and lands back in
           settings, same as any other exit. */
        var m2 = BV.modal("replace folders BackupViewer did not create?", b, opts);
        keep.addEventListener("click", function () {
          m2.close(true);
          BV.toast(n ? (n + " loaded, " + blocked.length + " left alone")
                     : "nothing changed — the folders were left alone");
        });
        wipe.addEventListener("click", function () {
          /* only the blocked ones: the rest already landed on the first pass */
          var redo = blocked.map(function (x) { return x.key; });
          wipe.disabled = true;
          wipe.textContent = "replacing…";
          BV.api.call("sim_export", redo, true).then(function (r2) {
            m2.close(true);
            BV.toast((n + (r2.exported || []).length) +
                     " loaded — point the simulator at " + r2.root, 4000);
          }).catch(function (e) {
            wipe.disabled = false;
            wipe.textContent = "replace them";
            BV.toast(e.message);
          });
        });
      }
    }).catch(function (e) { BV.toast(e.message); });
  };
}());
