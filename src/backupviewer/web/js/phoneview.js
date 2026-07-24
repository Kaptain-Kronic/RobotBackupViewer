/* phoneview.js - hand a phone a live picture over a QR handoff.

   BV.openViewfinder() mirrors the Matrox window (this app's own window, with
   the remote overlay up) to a phone: no rectangle to pick, no extra window -
   the PC grabs the app window's content live and the phone shows exactly that,
   following it if you move or resize it. Scan the QR, close this (keeps
   sharing), and the phone shows the Matrox.

   BV.openPhoneView(ip, label) is the camera-direct variant (relays the MTX
   HMI frame without touching the screen), kept for callers that want it.

   Both open the same modal: a scannable QR of http://<this pc>:<port>/v/
   <token>, chips to pick WHICH of this PC's addresses the QR dials
   (mobile-hotspot net first - the usual answer when the laptop is wired to
   the robot network), and a live status line that flips the moment a phone
   actually pulls a frame. The share keeps serving when the modal closes;
   "stop sharing" ends it. The phone only ever needs a route to the PC. */
(function () {
  "use strict";

  /* matrix -> inline SVG. Always dark-on-white with a quiet zone, whatever
     the theme - scanners want the contrast, not our palette. */
  function qrSvg(m) {
    var n = m.size, q = 4, s = n + 2 * q, d = "";
    for (var r = 0; r < n; r++) {
      var row = m.rows[r];
      for (var c = 0; c < n; c++) {
        if (row.charCodeAt(c) === 49) d += "M" + (c + q) + " " + (r + q) + "h1v1h-1z";
      }
    }
    return '<svg viewBox="0 0 ' + s + " " + s + '" shape-rendering="crispEdges" role="img"' +
      ' aria-label="QR code for the phone view URL" style="display:block;width:100%;height:auto">' +
      '<rect width="100%" height="100%" fill="#fff"/><path d="' + d + '" fill="#000"/></svg>';
  }

  BV.openPhoneView = function (ip, label) {
    BV.api.call("phone_view_start", { ip: ip, label: label || "" }).then(function (d) {
      show(label || ip, d, false);
    }).catch(function (e) { BV.toast("phone view: " + e.message); });
  };

  /* mirror the Matrox window: the QR is ready immediately - no pick step */
  BV.openViewfinder = function () {
    BV.api.call("viewfinder_start").then(function (d) {
      show("Matrox window", d, true);
    }).catch(function (e) { BV.toast("phone view: " + e.message); });
  };

  /* the firewall help panel, built once per modal and toggled by the "?".
     Reads the live command + rule state from the bridge; offers copy and a
     one-click elevated "add the rule" (UAC). */
  function toggleFirewallHelp(wrap) {
    if (wrap._fwPanel) {
      wrap._fwPanel.style.display = wrap._fwPanel.style.display === "none" ? "block" : "none";
      return;
    }
    var panel = BV.el("div", { style:
      "margin-top:0.6rem;padding:0.7rem;border:1px solid var(--edge);border-radius:8px;" +
      "background:var(--bg);font-size:0.82rem" });
    panel.appendChild(BV.el("div", { style: "font-weight:600;margin-bottom:0.3rem" },
      "phone says “server stopped responding”?"));
    panel.appendChild(BV.el("div", { class: "dim", style: "margin-bottom:0.5rem" },
      "the phone is reaching this pc, but <b>windows firewall</b> is blocking the " +
      "port on this network. open it once (needs admin) — then reload the page on the phone:"));
    var cmdBox = BV.el("div", { style:
      "font-family:Consolas,monospace;font-size:0.74rem;white-space:pre-wrap;" +
      "word-break:break-all;background:var(--bg2);border:1px solid var(--edge);" +
      "border-radius:6px;padding:0.45rem;user-select:text;margin-bottom:0.45rem" }, "loading…");
    var note = BV.el("div", { class: "dim", style: "margin-bottom:0.5rem" }, "");
    var btns = BV.el("div", { style: "display:flex;gap:0.5rem;flex-wrap:wrap" });
    var copyBtn = BV.el("button", { class: "btn" }, "copy command");
    var runBtn = BV.el("button", { class: "btn" }, "add the rule (admin)");
    btns.appendChild(copyBtn); btns.appendChild(runBtn);
    panel.appendChild(cmdBox); panel.appendChild(note); panel.appendChild(btns);
    wrap.appendChild(panel);
    wrap._fwPanel = panel;

    var cmd = "";
    function loadStatus() {
      BV.api.call("phone_view_firewall_status").then(function (fw) {
        cmd = fw.command;
        cmdBox.textContent = cmd;
        note.innerHTML = fw.rule_present
          ? '<span style="color:var(--ok)">✓ the firewall rule is already added</span> — ' +
            "if the phone still can't connect, it's a network issue, not the firewall."
          : "not added yet — click <b>add the rule</b> (approve the Windows prompt), " +
            "or paste the command into an <b>admin</b> PowerShell.";
      }).catch(function (e) {
        cmdBox.textContent = "(could not read the firewall state: " + BV.esc(e.message) + ")";
      });
    }
    copyBtn.addEventListener("click", function () { if (cmd) BV.copyText(cmd, "command copied"); });
    runBtn.addEventListener("click", function () {
      note.textContent = "approve the Windows admin prompt…";
      BV.api.call("phone_view_firewall_fix").then(function () {
        setTimeout(loadStatus, 3500);
      }).catch(function (e) { note.textContent = "could not start: " + e.message; });
    });
    loadStatus();
  }

  function show(label, d, windowMode) {
    var urls = d.urls;

    var wrap = BV.el("div", { style:
      "display:flex;flex-direction:column;gap:0.7rem;max-width:26rem" });

    var qrBox = BV.el("div", { style:
      "background:#fff;border-radius:10px;padding:0.75rem;align-self:center;" +
      "width:min(60vw,16rem)" });
    var urlLine = BV.el("div", { class: "dim", title: "click to copy", style:
      "font-family:Consolas,monospace;font-size:0.8rem;text-align:center;" +
      "cursor:pointer;user-select:text;word-break:break-all" });
    urlLine.addEventListener("click", function () {
      BV.copyText(urlLine.textContent, "address copied");
    });

    function pick(i) {
      var u = urls[i];
      urlLine.textContent = u.url;
      qrBox.innerHTML = '<div class="dim" style="text-align:center">…</div>';
      BV.api.call("phone_view_qr", { text: u.url }).then(function (m) {
        qrBox.innerHTML = qrSvg(m);
      }).catch(function (e) {
        qrBox.innerHTML = '<div class="dim" style="text-align:center;color:#000;padding:0.5rem">' +
          "no QR (" + BV.esc(e.message) + ") — type the address instead</div>";
      });
    }

    wrap.appendChild(qrBox);
    wrap.appendChild(urlLine);

    if (urls.length > 1) {
      var seg = BV.segmented(urls.map(function (u, i) {
        return { id: String(i), label: u.ip, title: "reach this pc via its " + u.kind + " address" };
      }), { value: "0", onChange: function (id) { pick(+id); } });
      seg.el.style.justifyContent = "center";
      seg.el.style.flexWrap = "wrap";
      wrap.appendChild(seg.el);
    }

    var status = BV.el("div", { class: "dim", style: "text-align:center" }, "starting…");
    wrap.appendChild(status);

    wrap.appendChild(BV.el("div", { class: "dim", style: "font-size:0.78rem" },
      "scan with the phone camera. the phone must reach <b>this pc</b>: same wifi, " +
      "or turn on windows <b>mobile hotspot</b>, join it from the phone, and pick the " +
      "hotspot address above. " +
      (windowMode
        ? "the phone mirrors the matrox window — scan, then <b>close (keeps sharing)</b> " +
          "so the camera fills the window again. "
        : "the pc relays the camera, so the phone never needs the robot network. ") +
      "the first share may pop a windows firewall prompt — allow it."));

    var row = BV.el("div", { style: "display:flex;gap:0.5rem;justify-content:flex-end" });
    var stopBtn = BV.el("button", { class: "btn" }, "stop sharing");
    var closeBtn = BV.el("button", { class: "btn" }, "close (keeps sharing)");
    row.appendChild(stopBtn);
    row.appendChild(closeBtn);
    wrap.appendChild(row);

    var poll = null;
    var modal = BV.modal("phone view · " + label, wrap, {
      onClose: function () { clearInterval(poll); },
    });

    /* corner "?" — the firewall fix for "server stopped responding" (the phone
       reaches the pc but Windows drops the port on this network profile) */
    modal.el.style.position = "relative";
    var helpBtn = BV.el("button", { class: "btn", title: "phone can't connect? (firewall help)",
      style: "position:absolute;top:0.55rem;right:0.55rem;width:1.7rem;height:1.7rem;" +
        "padding:0;border-radius:50%;line-height:1" }, "?");
    modal.el.appendChild(helpBtn);
    helpBtn.addEventListener("click", function () { toggleFirewallHelp(wrap); });

    closeBtn.addEventListener("click", function () { modal.close(); });
    stopBtn.addEventListener("click", function () {
      BV.api.call("phone_view_stop", { token: d.token }).then(function () {
        BV.toast("stopped sharing");
        modal.close();
      }).catch(function (e) { BV.toast("could not stop: " + e.message); });
    });

    function refresh() {
      BV.api.call("phone_view_status").then(function (st) {
        var s = (st.sessions || []).filter(function (x) { return x.token === d.token; })[0];
        if (!s) { status.textContent = "share ended"; return; }
        var watching = s.phones && s.last_pull_ms !== null && s.last_pull_ms <= 15000;
        var who = watching
          ? '<span style="color:var(--ok)">' + s.phones +
            (s.phones === 1 ? " phone" : " phones") + " watching</span>"
          : "no phone watching yet — scan the code";
        if (s.fetch_err) {
          status.innerHTML = '<span style="color:var(--error)">' +
            (s.kind === "window" ? "window capture failing" : "camera not answering") +
            '</span> <span class="dim">· ' + BV.esc(s.fetch_err) + "</span>";
        } else {
          status.innerHTML = who +
            (watching && s.frame_age_ms !== null
              ? ' <span class="dim">· frame ' + (s.frame_age_ms / 1000).toFixed(1) + "s old</span>"
              : "");
        }
      }).catch(function () { /* keep the last status through a hiccup */ });
    }
    poll = setInterval(refresh, 2000);
    refresh();

    pick(0);
  }
})();
