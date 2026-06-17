/* tabs/frames.js - vertical pendant-style frame cards:
     TOOL 1 · PH02 PIN [active]
     x : 16.649
     y : 737.058
     ...
     config : N U T, 0, 0, 0
   laid out in a grid so multi-TCP robots fill the screen. */
(function () {
  "use strict";

  var KIND_LABEL = { tools: "tool", frames: "uframe", jogs: "jog" };
  var SECTION_LABEL = {
    tools: "tool frames (utool)", frames: "user frames (uframe)",
    jogs: "jog frames", payloads: "payloads",
  };

  function isZero(e) {
    return ["x", "y", "z", "w", "p", "r"].every(function (ax) { return !e[ax]; });
  }

  function frameCard(kind, e, active) {
    if (kind === "payloads") return payloadCard(e);
    return BV.frameCard({
      title: KIND_LABEL[kind] + " " + e.index,
      pills: active ? [["active", "acc"]] : [],
      subtitle: e.comment,
      uninitialized: e.uninit || (e.x === undefined && e.w === undefined),
      axes: ["x", "y", "z", "w", "p", "r"].map(function (ax) { return [ax, BV.fmt.num(e[ax])]; }),
      config: e.config,
    });
  }

  /* a payload schedule card (mass / CG / inertia) - payloads live under the
     frames tab as their own section (pendant: SYSTEM>Motion>Payload) */
  function pnum(v) { return v == null ? null : BV.fmt.num(v, 3); }
  function payloadCard(s) {
    return BV.frameCard({
      title: "payload " + s.index,
      subtitle: s.comment || undefined,
      uninitialized: s.uninit,
      axes: [
        ["mass", s.mass == null ? null : BV.fmt.num(s.mass, 1) + " kg"],
        ["cg x", pnum(s.cg[0])], ["cg y", pnum(s.cg[1])], ["cg z", pnum(s.cg[2])],
        ["ix", pnum(s.inertia[0])], ["iy", pnum(s.inertia[1])], ["iz", pnum(s.inertia[2])],
      ],
    });
  }
  function payloadEmpty(s) { return !!s.uninit; }

  function render(view, toolbar, params) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    Promise.all([
      BV.api.call("get_frames").catch(function () { return { tools: {}, frames: {}, jogs: {}, active_tool: {}, active_frame: {} }; }),
      BV.api.call("get_payloads").catch(function () { return { groups: {} }; }),
    ]).then(function (res) {
      var fr = res[0];
      fr.payloads = (res[1] && res[1].groups) || {};   /* payloads = a section under frames */
      var groupSet = {};
      ["tools", "frames", "payloads"].forEach(function (k) {
        Object.keys(fr[k] || {}).forEach(function (g) { groupSet[g] = 1; });
      });
      var groups = Object.keys(groupSet).sort();
      if (!groups.length) groups = ["1"];
      var curGroup = params && params[0] && groups.indexOf(params[0]) >= 0 ? params[0] : groups[0];
      var showEmpty = false;

      if (groups.length > 1) {
        var seg = BV.segmented(
          groups.map(function (g) { return { id: g, label: "group " + g }; }),
          { value: curGroup, onChange: function (id) { curGroup = id; draw(); } }
        );
        toolbar.appendChild(seg.el);
      }
      var zt = BV.el("button", { class: "btn" }, "show empty");
      zt.addEventListener("click", function () {
        showEmpty = !showEmpty;
        zt.textContent = showEmpty ? "hide empty" : "show empty";
        draw();
      });
      toolbar.appendChild(zt);

      /* vs controls live at the END of the toolbar on every tab */
      var vs = false;
      var frB = null;
      var hlState = null;
      if (BV.state.compare) {
        var vsBtn = BV.el("button", {
          class: "btn",
          title: "show both robots' frames side by side",
        }, "vs " + (BV.state.compare.robot_name || BV.state.compare.name));
        var hlWrap = BV.el("div", { style: "display:none;gap:.75rem;align-items:center" });
        vsBtn.addEventListener("click", function () {
          vs = !vs;
          vsBtn.classList.toggle("primary", vs);
          hlWrap.style.display = vs ? "flex" : "none";
          if (vs && !frB) {
            BV.api.call("get_frames", "b").then(function (fb) {
              frB = fb;
              draw();
            }).catch(function (e) {
              vs = false;
              vsBtn.classList.remove("primary");
              hlWrap.style.display = "none";
              BV.toast(e.message);
            });
          } else draw();
        });
        toolbar.appendChild(vsBtn);
        hlState = BV.vsDiff.controls(hlWrap, function () { draw(); });
        toolbar.appendChild(hlWrap);
      }

      var wrap = BV.el("div");
      view.appendChild(wrap);

      function frameDiffers(a, b, mode) {
        var cmt = (a.comment || "") !== (b.comment || "");
        var val = ["x", "y", "z", "w", "p", "r"].some(function (ax) {
          return Math.abs((a[ax] || 0) - (b[ax] || 0)) > 0.0005;
        }) || !!a.uninit !== !!b.uninit;
        if (mode === "no_comments") return val;
        if (mode === "no_values") return cmt;
        return cmt || val;
      }

      function section(kind, label, activeNum, target, data, otherData) {
        target = target || wrap;
        data = data || fr;
        var entries = (data[kind] || {})[curGroup] || [];
        var shown = entries.filter(function (e) {
          if (kind === "payloads") return showEmpty || !payloadEmpty(e);
          return showEmpty || e.comment || (!e.uninit && !isZero(e)) || e.index === activeNum;
        });
        if (!shown.length) return;

        /* collapsible, larger header so a tech can hide payloads or a whole
           frame type (right-click the header = collapse/expand all) */
        var sec = BV.el("div", { class: "frame-section" });
        var head = BV.el("div", { class: "frame-section-h" },
          BV.esc(label) + ' <span class="cnt">' + shown.length + "/" + entries.length + "</span>");
        var body = BV.el("div");   /* collapse body wrapper, so .cards keeps its grid display */
        var grid = BV.el("div", { class: "cards", style: "grid-template-columns:repeat(auto-fill,minmax(230px,1fr))" });
        body.appendChild(grid);
        var otherEntries = otherData ? ((otherData[kind] || {})[curGroup] || []) : null;
        shown.forEach(function (e) {
          var c = frameCard(kind, e, e.index === activeNum);
          if (otherEntries && hlState && hlState.on) {
            var twin = otherEntries.find(function (o) { return o.index === e.index; });
            if (!twin || frameDiffers(e, twin, hlState.mode)) c.classList.add("vsdiff");
          }
          grid.appendChild(c);
        });
        sec.appendChild(head);
        sec.appendChild(body);
        target.appendChild(sec);
        BV.collapsible(sec, head, body, { open: true });
      }

      function sideColumn(data, otherData, name, active) {
        var col = BV.el("div", { style: "min-width:0" });
        col.insertAdjacentHTML("beforeend",
          '<div class="mt-label" style="font-size:.92rem">' + BV.esc(name) + "</div>");
        section("tools", "tool frames (utool)", active.tool, col, data, otherData);
        section("frames", "user frames (uframe)", active.frame, col, data, otherData);
        section("jogs", "jog frames", undefined, col, data, otherData);
        if (col.childElementCount <= 1) {
          col.insertAdjacentHTML("beforeend", '<div class="dim" style="font-size:.8rem">no frame data for group ' +
            BV.esc(curGroup) + "</div>");
        }
        return col;
      }

      function draw() {
        wrap.innerHTML = "";
        if (vs && frB) {
          var nameA = BV.state.manifest.robot_name || BV.state.manifest.name;
          var nameB = BV.state.compare.robot_name || BV.state.compare.name;
          var grid = BV.el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;align-items:start" });
          grid.appendChild(sideColumn(fr, frB, nameA,
            { tool: fr.active_tool[curGroup], frame: fr.active_frame[curGroup] }));
          grid.appendChild(sideColumn(frB, fr, nameB,
            { tool: (frB.active_tool || {})[curGroup], frame: (frB.active_frame || {})[curGroup] }));
          wrap.appendChild(grid);
          return;
        }
        section("tools", "tool frames (utool)", (fr.active_tool || {})[curGroup]);
        section("frames", "user frames (uframe)", (fr.active_frame || {})[curGroup]);
        section("jogs", "jog frames");
        section("payloads", "payloads");
        if (!wrap.children.length) {
          wrap.innerHTML = '<div class="empty-state"><div class="big">no frame or payload data</div>' +
            '<div class="hint">try “show empty”</div></div>';
        }
      }
      draw();
    }).catch(function (e) {
      view.innerHTML = '<div class="empty-state"><div class="big">frames unavailable</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "frames", label: "frames/payload", render: render });
})();
