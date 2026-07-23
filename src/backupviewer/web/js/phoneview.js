/* phoneview.js - hand a phone the camera's live picture (QR handoff).

   BV.openPhoneView(ip, label) starts (or rejoins) a share via the bridge and
   opens a modal: a scannable QR of http://<this pc>:<port>/v/<token>, chips
   to pick WHICH of this PC's addresses the QR dials (mobile-hotspot net
   first - the usual answer when the laptop is wired to the robot network),
   and a live status line that flips the moment a phone actually pulls a
   frame. The share keeps serving when the modal closes; "stop sharing" ends
   it. The PC relays the camera's HMI frame, so the phone only ever needs a
   route to the PC - never to the camera VLAN. */
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
      show(label || ip, d);
    }).catch(function (e) { BV.toast("phone view: " + e.message); });
  };

  function show(label, d) {
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
      "hotspot address above. the pc relays the camera, so the phone never needs the " +
      "robot network. the first share may pop a windows firewall prompt — allow it."));

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
        if (s.fetch_err) {
          status.innerHTML = '<span style="color:var(--error)">camera not answering</span>' +
            ' <span class="dim">· ' + BV.esc(s.fetch_err) + "</span>";
        } else if (!s.phones || s.last_pull_ms === null || s.last_pull_ms > 15000) {
          status.textContent = "no phone watching yet — scan the code";
        } else {
          status.innerHTML = '<span style="color:var(--ok)">' + s.phones +
            (s.phones === 1 ? " phone" : " phones") + " watching</span>" +
            (s.frame_age_ms !== null
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
