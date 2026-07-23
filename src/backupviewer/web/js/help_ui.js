/* help_ui.js - the ? help window: guide | shortcuts, in the same
   two-tab-window shape as 🎨 (theme_ui.js).

   - guide:     topic list (left, filterable) + the topic's doc (right),
                each pane with its own scroll. content comes verbatim from
                help_content.js; this file renders and never writes words.
   - shortcuts: the keyboard map (rows from BV.help.shortcuts — they moved
                here from keys.js so the map has one home).

   Doc links: <a data-topic='id'> hops topics, <a data-tab='shortcuts'>
   switches window tab, <a data-goto='#hash'> closes and routes there.
   The window remembers its last tab + topic for the session, so ? twice
   in a row puts you back where you were. Opens from the topbar ? button
   or the ? key. */
(function () {
  "use strict";

  var lastTab = "guide";
  var lastTopic = null;

  /* plain text of a topic (tags stripped), cached for filtering */
  function topicText(t) {
    if (t._text === undefined) {
      var d = document.createElement("div");
      d.innerHTML = t.body;
      t._text = (t.title + " " + d.textContent).toLowerCase();
    }
    return t._text;
  }

  BV.helpUI = {
    /* open(topicId?) - a topicId forces the guide tab onto that topic
       (the future first-run / tips entry point) */
    open: function (topicId) {
      var topics = BV.help.topics;
      var activeTab = topicId ? "guide" : lastTab;
      var curId = topicId || lastTopic || (topics[0] || {}).id;
      var guide = null;   /* {onKey, filterEl} while the guide tab is up */

      var body = BV.el("div");
      var seg = BV.el("div", { class: "seg help-tabs" });
      var slot = BV.el("div", { class: "help-slot" });
      body.appendChild(seg);
      body.appendChild(slot);

      function switchTab(next) {
        activeTab = next;
        lastTab = next;
        guide = null;
        seg.querySelectorAll("button").forEach(function (b) {
          b.classList.toggle("active", b.dataset.tab === next);
        });
        slot.innerHTML = "";
        slot.appendChild(next === "guide" ? buildGuide() : buildShortcuts());
      }
      ["guide", "shortcuts"].forEach(function (id) {
        var b = BV.el("button", { class: id === activeTab ? "active" : "" }, id);
        b.dataset.tab = id;
        b.addEventListener("click", function () { switchTab(id); });
        seg.appendChild(b);
      });

      /* ---- guide: topic nav + doc ---- */
      function buildGuide() {
        var wrap = BV.el("div", { class: "help-body" });
        var nav = BV.el("div", { class: "help-nav" });
        var doc = BV.el("div", { class: "help-doc" });

        var filter = BV.el("input", {
          class: "help-filter", type: "text",
          placeholder: "filter topics…", spellcheck: "false",
        });
        nav.appendChild(filter);
        var none = BV.el("div", { class: "help-none dim hidden" }, "no topics match");
        nav.appendChild(none);

        function visRows() {
          return Array.prototype.slice.call(
            nav.querySelectorAll(".opt-row[data-topic-id]:not(.hidden)"));
        }

        function select(id) {
          var t = topics.find(function (x) { return x.id === id; });
          if (!t) return;
          curId = id;
          lastTopic = id;
          nav.querySelectorAll(".opt-row").forEach(function (r) {
            r.classList.toggle("sel", r.dataset.topicId === id);
          });
          var sel = nav.querySelector(".opt-row.sel");
          if (sel) sel.scrollIntoView({ block: "nearest" });
          doc.innerHTML = "<h2 class='help-title'>" + BV.esc(t.title) + "</h2>" + t.body;
          doc.scrollTop = 0;
        }

        topics.forEach(function (t) {
          var row = BV.el("div", { class: "opt-row" },
            '<span class="name">' + BV.esc(t.title) + "</span>");
          row.dataset.topicId = t.id;
          row.addEventListener("click", function () { select(t.id); });
          nav.appendChild(row);
        });

        function applyFilter() {
          var q = filter.value.trim().toLowerCase();
          var hits = 0;
          topics.forEach(function (t) {
            var row = nav.querySelector('.opt-row[data-topic-id="' + t.id + '"]');
            var hit = !q || topicText(t).indexOf(q) >= 0;
            row.classList.toggle("hidden", !hit);
            if (hit) hits++;
          });
          none.classList.toggle("hidden", !!hits);
        }
        filter.addEventListener("input", applyFilter);

        /* links inside the doc */
        doc.addEventListener("click", function (e) {
          var a = e.target.closest("a");
          if (!a) return;
          e.preventDefault();
          if (a.dataset.topic) select(a.dataset.topic);
          else if (a.dataset.tab) switchTab(a.dataset.tab);
          else if (a.dataset.goto) { modal.close(true); location.hash = a.dataset.goto; }
        });

        guide = {
          filterEl: filter,
          onKey: function (e) {
            if (document.activeElement === filter) return false;  /* let typing type */
            if (e.key === "/") { filter.focus(); return true; }
            if (e.key === "ArrowDown" || e.key === "j" ||
                e.key === "ArrowUp" || e.key === "k") {
              var rows = visRows();
              if (!rows.length) return true;
              var i = rows.findIndex(function (r) { return r.dataset.topicId === curId; });
              var delta = (e.key === "ArrowDown" || e.key === "j") ? 1 : -1;
              var next = rows[i < 0 ? 0 : (i + delta + rows.length) % rows.length];
              select(next.dataset.topicId);
              return true;
            }
            return false;
          },
        };

        wrap.appendChild(nav);
        wrap.appendChild(doc);
        select(curId);
        return wrap;
      }

      /* ---- shortcuts: the keyboard map ---- */
      function buildShortcuts() {
        var wrap = BV.el("div", { class: "help-shortcuts" });
        wrap.innerHTML = BV.help.shortcuts.map(function (r) {
          return '<div class="static-row"><span class="name">' + BV.esc(r[1]) +
            "</span><span><kbd>" + BV.esc(r[0]) + "</kbd></span></div>";
        }).join("");
        return wrap;
      }

      var modal = BV.modal("help", body, {
        /* esc with a live filter clears it instead of closing (the compare
           picker's contract); a second esc closes */
        beforeClose: function () {
          if (guide && guide.filterEl.value) {
            guide.filterEl.value = "";
            guide.filterEl.dispatchEvent(new Event("input"));
            return false;
          }
          return true;
        },
        onKey: function (e) {
          if (activeTab === "guide" && guide) return guide.onKey(e);
          return false;
        },
      });
      modal.el.classList.add("help-win");

      switchTab(activeTab);
    },
  };
})();
