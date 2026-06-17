/* tabs/backup.js - the "take a new backup" screen (#backup).
   shell:true so it renders with no backup open. Fill a robot's FTP details
   (or pick a saved robot), pre-flight with "probe", then "start backup" pulls
   the MD: device over FTP into the Latest + dated tree, polling live progress.
   The password (if any) is prompted per run and held in memory only. */
(function () {
  "use strict";

  var _poll = null;
  var _jobId = null;

  function stopPoll() { if (_poll) { clearInterval(_poll); _poll = null; } }

  /* ---- form ---- */

  function inp(ph, attrs) {
    var a = { type: "text", class: "lf-input", spellcheck: "false", placeholder: ph || "" };
    if (attrs) Object.keys(attrs).forEach(function (k) { a[k] = attrs[k]; });
    return BV.el("input", a);
  }
  function row(label, el) {
    var r = BV.el("div", { class: "lf-row" });
    r.appendChild(BV.el("label", null, BV.esc(label)));
    r.appendChild(el);
    return r;
  }

  function buildForm() {
    var el = BV.el("div", { class: "backup-form" });
    var picker = BV.el("div", { class: "bf-picker" });
    el.appendChild(picker);

    var host = inp("192.168.1.100");
    var robot = inp("robot name");
    var line = inp("line");
    var plant = inp("plant");
    var user = inp("(anonymous)");
    var passive = BV.el("input", { type: "checkbox", class: "lf-check" });
    passive.checked = true;
    var dest = inp("C:\\RobotBackups");
    var note = inp("optional note");

    el.appendChild(row("ip / host", host));
    el.appendChild(row("robot", robot));
    el.appendChild(row("line", line));
    el.appendChild(row("plant", plant));
    el.appendChild(row("ftp user", user));
    el.appendChild(row("passive ftp", passive));
    el.appendChild(row("save to", dest));
    el.appendChild(row("note", note));

    BV.api.call("get_settings").then(function (s) {
      if (s && s.backup_root && !dest.value) dest.value = s.backup_root;
    }).catch(function () {});

    function get() {
      return {
        host: host.value.trim(), robot: robot.value.trim(),
        line: line.value.trim(), plant: plant.value.trim(),
        user: user.value.trim(), passive: passive.checked,
        dest_root: dest.value.trim(), note: note.value.trim(),
      };
    }

    function mountPicker(robots) {
      var usable = robots.filter(function (r) { return (r.ips && r.ips.length) || r.robot; });
      if (!usable.length) return;
      picker.innerHTML = '<div class="bf-picker-label">from library</div>';
      var chips = BV.el("div", { class: "bf-chips" });
      usable.forEach(function (r) {
        var chip = BV.el("button", { class: "btn bf-chip" }, BV.esc(r.robot || r.ips[0]));
        if (r.ips && r.ips[0]) chip.title = r.ips[0];
        chip.addEventListener("click", function () {
          host.value = (r.ips && r.ips[0]) || "";
          robot.value = r.robot || "";
          line.value = r.line || "";
          plant.value = r.plant || "";
          user.value = (r.ftp && r.ftp.user) || "";
          passive.checked = !r.ftp || r.ftp.passive !== false;
        });
        chips.appendChild(chip);
      });
      picker.appendChild(chips);
    }

    return { el: el, get: get, mountPicker: mountPicker };
  }

  /* ---- probe (pre-flight) ---- */

  function doProbe(form, status, btn) {
    var spec = form.get();
    if (!spec.host) { BV.toast("enter an ip / host"); return; }
    btn.disabled = true;
    status.innerHTML = '<div class="dim">probing ' + BV.esc(spec.host) + "…</div>";
    BV.api.call("probe_controller", spec).then(function (r) {
      var pills = BV.pill(r.reachable ? "reachable" : "unreachable", r.reachable ? "on" : "err");
      if (r.reachable) {
        pills += " " + BV.pill("MD " + (r.has_md ? "✓" : "✗"), r.has_md ? "acc" : "ghost");
        pills += " " + BV.pill("FR " + (r.has_fr ? "✓" : "✗"), r.has_fr ? "acc" : "ghost");
      }
      var html = '<div class="bf-probe">' + pills + "</div>";
      if (r.banner) html += '<div class="bf-banner">' + BV.esc(r.banner) + "</div>";
      if (r.error) html += '<div class="bf-err">' + BV.esc(r.error) + "</div>";
      status.innerHTML = html;
    }).catch(function (e) {
      status.innerHTML = '<div class="bf-err">' + BV.esc(e.message) + "</div>";
    }).then(function () { btn.disabled = false; });
  }

  /* ---- start + progress ---- */

  function promptPasswordIfNeeded(spec, cont) {
    if (!spec.user) { spec.passwd = ""; cont(spec); return; }
    var body = BV.el("div", { class: "lib-form" });
    var pw = BV.el("input", { type: "password", class: "lf-input", spellcheck: "false" });
    body.appendChild(row("password", pw));
    var acts = BV.el("div", { class: "lf-actions" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var ok = BV.el("button", { class: "btn primary" }, "connect");
    acts.appendChild(cancel);
    acts.appendChild(ok);
    body.appendChild(acts);
    var m = BV.modal("ftp password for " + spec.user, body);
    pw.focus();
    cancel.addEventListener("click", m.close);
    function go() { spec.passwd = pw.value; m.close(); cont(spec); }
    ok.addEventListener("click", go);
    body.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
  }

  function doStart(form, status, btn) {
    var spec = form.get();
    if (!spec.host) { BV.toast("enter an ip / host"); return; }
    if (!spec.robot) { BV.toast("enter a robot name"); return; }
    promptPasswordIfNeeded(spec, function (full) {
      btn.disabled = true;
      BV.api.call("start_backup", full).then(function (res) {
        _jobId = res.job_id;
        pollProgress(status, btn);
      }).catch(function (e) {
        status.innerHTML = '<div class="bf-err">' + BV.esc(e.message) + "</div>";
        btn.disabled = false;
      });
    });
  }

  function statusText(p) {
    return {
      connecting: "connecting…", listing: "listing files…",
      downloading: "downloading…", done: "done", error: "failed",
      cancelled: "cancelled", pending: "starting…",
    }[p.status] || p.status;
  }

  function pollProgress(status, startBtn) {
    stopPoll();
    renderProgress(status, { status: "connecting", total: 0, done: 0 }, startBtn);
    _poll = setInterval(function () {
      if (!document.body.contains(status)) { stopPoll(); return; }  /* navigated away */
      BV.api.call("get_backup_progress", _jobId).then(function (p) {
        renderProgress(status, p, startBtn);
        if (p.status === "done" || p.status === "error" || p.status === "cancelled") {
          stopPoll();
          startBtn.disabled = false;
          if (p.status === "done") BV.toast("backup complete · " + p.done + " files");
        }
      }).catch(function () { stopPoll(); startBtn.disabled = false; });
    }, 500);
  }

  function renderProgress(status, p, startBtn) {
    var running = (p.status === "connecting" || p.status === "listing"
      || p.status === "downloading" || p.status === "pending");
    var pct = p.total ? Math.round(100 * p.done / p.total) : (running ? 8 : 0);

    var box = BV.el("div", { class: "backup-progress" });
    var bar = BV.el("div", { class: "membar" });
    bar.innerHTML =
      '<div class="mb-label"><span>' + BV.esc(statusText(p)) + "</span><span>" +
      (p.total ? p.done + " / " + p.total : "") + "</span></div>" +
      '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div>';
    box.appendChild(bar);

    if (p.current && running) {
      box.appendChild(BV.el("div", { class: "bf-current dim" }, BV.esc(p.current)));
    }
    if (running) {
      var cancel = BV.el("button", { class: "btn" }, "cancel");
      cancel.addEventListener("click", function () { BV.api.call("cancel_backup", _jobId); });
      box.appendChild(cancel);
    } else if (p.status === "done") {
      box.appendChild(doneActions(p));
    } else if (p.status === "error") {
      box.appendChild(BV.el("div", { class: "bf-err" }, "backup failed: " + BV.esc(p.error)));
    } else if (p.status === "cancelled") {
      box.appendChild(BV.el("div", { class: "dim" }, "cancelled"));
    }
    status.innerHTML = "";
    status.appendChild(box);
  }

  function doneActions(p) {
    var el = BV.el("div", { class: "bf-done" });
    var summary = BV.pill(p.done + " files · " + BV.fmt.bytes(p.bytes), "on");
    if (p.skipped && p.skipped.length) summary += " " + BV.pill(p.skipped.length + " skipped", "ghost");
    el.innerHTML = '<div class="bf-done-pills">' + summary + "</div>";

    var acts = BV.el("div", { class: "bf-done-acts" });
    var open = BV.el("button", { class: "btn primary" }, "open backup");
    open.addEventListener("click", function () {
      BV.api.call("open_backup", p.latest_path || p.dated_path).then(function (m) {
        BV.state.setManifest(m);
        location.hash = "#overview";
      }).catch(function (e) { BV.toast(e.message); });
    });
    var lib = BV.el("button", { class: "btn" }, "library");
    lib.addEventListener("click", function () { BV.goHome(); });
    acts.appendChild(open);
    acts.appendChild(lib);
    el.appendChild(acts);
    return el;
  }

  /* ---- screen ---- */

  function render(view, toolbar, params) {
    stopPoll();

    var crumb = BV.el("div", { class: "crumb" });
    var back = BV.el("span", { class: "back" }, "‹ home");
    back.addEventListener("click", function () { BV.goHome(); });
    crumb.appendChild(back);
    crumb.appendChild(BV.el("span", { class: "title" }, "take a new backup"));
    view.appendChild(crumb);

    var card = BV.card({ title: "robot" });
    view.appendChild(card.el);

    var form = buildForm();
    card.el.appendChild(form.el);

    var actions = BV.el("div", { class: "backup-actions" });
    var probeBtn = BV.el("button", { class: "btn" }, "probe");
    var startBtn = BV.el("button", { class: "btn primary" }, "start backup");
    actions.appendChild(probeBtn);
    actions.appendChild(startBtn);
    card.el.appendChild(actions);

    var status = BV.el("div", { class: "backup-status" });
    view.appendChild(status);

    probeBtn.addEventListener("click", function () { doProbe(form, status, probeBtn); });
    startBtn.addEventListener("click", function () { doStart(form, status, startBtn); });

    var note = BV.el("div", { class: "bf-note dim" },
      "pulls the controller’s MD: device (all-of-above) over FTP. " +
      "image backups (TFTP/boot-menu) aren’t taken here.");
    view.appendChild(note);

    BV.api.call("lib_list").then(function (data) {
      form.mountPicker((data && data.robots) || []);
    }).catch(function () {});
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "backup", label: "backup", render: render, hidden: true, always: true, shell: true });
})();
