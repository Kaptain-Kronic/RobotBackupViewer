/* help_content.js - the guide's words, and nothing else. Pure data: an
   ordered list of topics (html strings) + the shortcut rows the shortcuts
   tab renders. help_ui.js owns all behavior.

   The honesty guard: every registered tab must have a topic claiming it
   (topic.tab) and every topic.tab must name a real registered tab — the
   help probe fails the build on either miss, so these words can never
   silently drift from what the app actually is. Features that aren't tabs
   (scan, backups, themes…) simply carry no tab field.

   Style: lowercase, short, plant voice. Links: <a data-topic='id'> hops
   inside the guide, <a data-tab='shortcuts'> switches window tab,
   <a data-goto='#hash'> closes help and routes there. Avoid exact counts
   that rot (number of checks, themes, effects) — name behaviors instead. */
(function () {
  "use strict";

  BV.help = {
    topics: [

      { id: "start", title: "getting started", body:
        "<p>backupviewer reads FANUC robot backups. point it at a folder of " +
        "them (the <a data-topic='library'>library</a>), click a robot, and " +
        "its latest complete backup opens — every tab up top is one view " +
        "into the same evidence.</p>" +
        "<p>tabs light up based on what the backup actually contains; a tab " +
        "that stays dark means those files aren't in this backup. the " +
        "number keys switch tabs, <kbd>ctrl</kbd>+<kbd>k</kbd> searches the " +
        "whole backup, and <kbd>?</kbd> opens this window any time.</p>" +
        "<h3>the two features people miss</h3>" +
        "<p><a data-topic='compare'>compare</a> diffs any two backups in one " +
        "window — no need to run two copies of the app side by side. and " +
        "<a data-topic='scan'>fleet scan</a> runs health checks across every " +
        "robot you select, not just the open one.</p>" +
        "<p class='dim'>the viewer is read-only — see " +
        "<a data-topic='honesty'>what it will never do</a>.</p>" },

      { id: "honesty", title: "what it will never do", body:
        "<p>the app is built to be trusted next to real equipment, so a few " +
        "promises hold everywhere:</p>" +
        "<p><strong>backups are read-only evidence.</strong> nothing in the " +
        "app ever writes into a backup folder. flagging problems is the " +
        "app's job; changing robots is yours.</p>" +
        "<p><strong>a half-taken backup never looks finished.</strong> a " +
        "pull that died mid-download shows <code>partial ⚠</code> and is " +
        "never offered as the latest snapshot.</p>" +
        "<p><strong>unverified meanings show a <code>?</code>.</strong> a " +
        "raw value is only mapped to a meaning when it's been proven " +
        "against a real controller — otherwise you get the raw value and an " +
        "honest question mark, never a guess.</p>" +
        "<p><strong>empty entries are hidden, not dropped.</strong> every " +
        "list that hides empty or disabled rows has a toggle to show them.</p>" +
        "<p><strong>diffs always show both sides,</strong> labeled, with " +
        "<code>non-existent</code> for a missing side — never a one-sided " +
        "row you have to interpret.</p>" },

      { id: "library", title: "the library", tab: "home", body:
        "<p>the home screen is your saved robot library, grouped " +
        "plant → line → robot in collapsible folders. click a robot to open " +
        "its backup; the checkbox selects it for line-level actions " +
        "(backup, <a data-topic='scan'>scan</a>, and the rest of the " +
        "actions row).</p>" +
        "<p><code>+ add robot</code> adds one from an existing backup " +
        "folder, by hand, or by discovering controllers on the network. " +
        "starring a robot (★) pins it — its cameras in tow — into the " +
        "favorites strip up top.</p>" +
        "<p>each robot row also holds its notes (first line always " +
        "visible) and a <code>⋯</code> menu for the rest: edit, manage " +
        "backups, open the folder on disk.</p>" +
        "<h3>two lenses</h3>" +
        "<p>the head toggle flips the same library between " +
        "<code>backup</code> (robots) and <code>multi-cam</code> (live " +
        "camera tiles) — see <a data-topic='cameras'>cameras</a>.</p>" +
        "<p class='dim'>the library folder itself is set in ⚙ settings; " +
        "<code>refresh library</code> re-reads it from disk. " +
        "<a data-goto='#home'>open the library</a></p>" },

      { id: "backups", title: "taking backups", body:
        "<p>select robots in the <a data-topic='library'>library</a> and hit " +
        "<code>backup</code>: the app pulls fresh FTP backups for all of " +
        "them, one gentle connection each, with live per-row progress. the " +
        "strip above the status bar tracks the run — you can keep browsing " +
        "while it works.</p>" +
        "<p>every run lands in a durable log, and <code>retry failed</code> " +
        "picks up exactly the robots that didn't make it. backups taken " +
        "mid-run join the open run instead of forking a new one.</p>" +
        "<h3>trusting what you took</h3>" +
        "<p>a backup only counts as complete when its completion marker was " +
        "written last — a job that died halfway shows " +
        "<code>partial ⚠</code> forever and is never picked as " +
        "latest. <code>manage backups</code> (in the library's actions row) " +
        "lists every dated snapshot per robot so you can prune old ones " +
        "safely, and the stale panel points at robots whose newest backup " +
        "is getting old.</p>" },

      { id: "compare", title: "compare two backups", tab: "compare", body:
        "<p>compare diffs two backups in one window — you never need two " +
        "copies of the app open. with your main backup open, hit " +
        "<code>compare</code> in the topbar and pick the second side " +
        "<code>from library</code> (any saved robot's snapshots) or " +
        "<code>from folder</code> (anywhere on disk).</p>" +
        "<p>the report is changes-only: one collapsible section per " +
        "category with <code>+added −removed ~changed</code> counts. both " +
        "sides are always labeled; a value missing on one side says " +
        "<code>non-existent</code>. click a changed program to see it " +
        "<a data-topic='pdiff'>line by line</a>.</p>" +
        "<p>while a compare is active, tabs like programs, frames and " +
        "macros grow a <code>vs</code> mode showing both robots side by " +
        "side with differences highlighted.</p>" +
        "<h3>compare over time</h3>" +
        "<p>same robot, different date: in <a data-topic='overview'>" +
        "overview</a>, the 🕓 button lists every dated snapshot — the " +
        "<code>vs</code> pill on any row diffs it against the open one. " +
        "that's the time-travel view: what changed on this robot since " +
        "tuesday.</p>" },

      { id: "pdiff", title: "program diff", tab: "pdiff", body:
        "<p>two programs aligned line by line in one scroller: unchanged " +
        "lines dimmed, changes highlighted, and a line that exists on only " +
        "one side gets padding on the other — the two columns never drift " +
        "out of step.</p>" +
        "<p>you reach it from a changed-program row in the " +
        "<a data-topic='compare'>compare report</a>, or from the " +
        "<a data-topic='programs'>programs</a> tab while a compare is " +
        "active (vs mode).</p>" },

      { id: "scan", title: "fleet health scan", body:
        "<p>the fleet scan runs health checks across many robots at once — " +
        "select them in the <a data-topic='library'>library</a> and hit " +
        "<code>scan</code>. it reads the robots' backups, not the live " +
        "controllers, so it's safe to run any time.</p>" +
        "<p>the picker groups checks by category — pick what you care " +
        "about. among them: unused programs, motion lines commented out " +
        "(the robot is skipping taught points), positions referenced but " +
        "never taught, programs reading uninitialized PRs, general " +
        "override left below 100%, controller clock drift, DCS options and " +
        "signatures, and mastering state. a <code>find</code> check " +
        "searches any text across the whole fleet — add several terms as " +
        "chips.</p>" +
        "<p>some checks have their own dial right on the picker row — " +
        "clock drift, for one, asks how much drift you'll tolerate. " +
        "results group per check with per-robot hits, and the report can " +
        "be copied out for a shift-note or an email.</p>" },

      { id: "search", title: "backup-wide search", tab: "search", body:
        "<p><kbd>ctrl</kbd>+<kbd>k</kbd> from anywhere — or the box in the " +
        "topbar — searches the whole open backup: programs, signal " +
        "comments, registers, system variables. results are grouped by " +
        "where they were found.</p>" +
        "<p>clicking any signal or register anywhere in the app runs the " +
        "same search — that's the fastest way to answer <em>who touches " +
        "DO[105]?</em></p>" +
        "<p class='dim'>signals match in TP notation — <code>DI[279]</code>, " +
        "not <code>DIN[279]</code> — and either spelling is normalized for " +
        "you.</p>" },

      { id: "overview", title: "overview", tab: "overview", body:
        "<p>the dashboard: hero and stat chips up top, then cards — " +
        "ethernet, motors, alarm history and the rest. drag a card by its " +
        "title to rearrange; the layout is remembered. collapse the ones " +
        "you don't use.</p>" +
        "<p>alarm history starts as a snapshot of the most recent entries " +
        "and expands into the full filterable table.</p>" +
        "<p>the 🕓 button switches between this robot's dated snapshots — " +
        "and its <code>vs</code> pill opens the " +
        "<a data-topic='compare'>time-travel diff</a>. " +
        "<code>📂 open location</code> reveals the open snapshot's folder " +
        "on disk.</p>" },

      { id: "programs", title: "programs", tab: "programs", body:
        "<p>every TP program in the backup. filter by name, show or hide " +
        "system and binary-only programs, star the styles you care about, " +
        "or flip to the style table.</p>" +
        "<p>open a program for highlighted source with a call panel and an " +
        "expandable call tree — who calls this, what it calls. IO and " +
        "register tokens in the source are clickable: one click runs a " +
        "<a data-topic='search'>backup-wide search</a> for that signal.</p>" +
        "<p>the macro table (name, program, assignment) lives behind the " +
        "<code>macros</code> button up top. in compare's vs mode both " +
        "robots' lists sit side by side with differences highlighted.</p>" },

      { id: "io", title: "io", tab: "io", body:
        "<p>signals organized like the pendant: category sub-tabs " +
        "(digital, group, analog, uop, sop, …) with IN and OUT side by " +
        "side — toggle either off when you only care about one direction.</p>" +
        "<p>the rack/slot/port assignment list jumps you straight to a " +
        "hardware address. click any signal to " +
        "<a data-topic='search'>search the whole backup</a> for it.</p>" },

      { id: "registers", title: "registers", tab: "registers", body:
        "<p>R, PR and SR under one tab, split into side-by-side columns on " +
        "wide screens to halve the scrolling. position registers show " +
        "their representation honestly — joint rows as J1…, cartesian as " +
        "xyzwpr.</p>" +
        "<p>empty registers are hidden by default — the " +
        "<code>show empty</code> toggle lists them, nothing is ever " +
        "silently dropped. click a register to " +
        "<a data-topic='search'>find every program that uses it</a>.</p>" },

      { id: "frames", title: "frames / payload", tab: "frames", body:
        "<p>tool frames, user frames and jog frames as pendant-style " +
        "cards — x y z w p r plus the config string — laid out in a grid " +
        "so a multi-TCP robot fills the screen instead of scrolling.</p>" +
        "<p>payload schedules sit in the same tab. in vs mode both " +
        "robots' frames and payloads line up side by side.</p>" },

      { id: "dcs", title: "dcs", tab: "dcs", body:
        "<p>dual check safety, laid out the way the pendant prints it. the " +
        "landing card is the quick health check: current signature vs " +
        "latched, at a glance. below it, each section of the DCS report " +
        "opens as its own page — zones and their geometry, safe IO, " +
        "safety logic shown as equations.</p>" +
        "<p>only meanings verified against a real controller are mapped to " +
        "words; anything unproven shows a <code>?</code> plus the raw " +
        "value (see <a data-topic='honesty'>what it will never do</a>). " +
        "cartesian zones can also be seen to scale in the " +
        "<a data-topic='view3d'>3d view</a>.</p>" },

      { id: "view3d", title: "3d view", tab: "view3d", body:
        "<p>DCS cartesian zones drawn to scale — press <kbd>0</kbd>. drag " +
        "to orbit, middle-drag or shift-drag to pan, wheel to zoom; the " +
        "cube in the corner snaps to any face, edge or corner, and " +
        "orthographic/perspective is one toggle. it renders as plain SVG, " +
        "so it works even on rescue-mode PCs with software rendering.</p>" +
        "<p>each DCS check is a row in the side panel: cartesian zones get " +
        "show/hide and a color swatch matching the viewport; joint and " +
        "speed checks carry their data only — there's nothing honest to " +
        "draw for them without a robot model. user model shapes " +
        "($DCSS_MODEL) draw at their true frames.</p>" +
        "<h3>the posed arm</h3>" +
        "<p>with FANUC's own kinematics imported (a one-time pull from a " +
        "Roboguide install — the app itself ships no FANUC data), the arm " +
        "poses itself from the backup's joint snapshot and self-checks " +
        "against the controller's printed TCP. a robot type without a " +
        "match honestly stays un-posed. rotation direction can be " +
        "inverted per axis in ⚙ settings.</p>" },

      { id: "sysvars", title: "system vars", tab: "sysvars", body:
        "<p>the controller's full <code>$</code>-variable dump as one " +
        "searchable tree. top-level records list instantly; a record's " +
        "body loads the first time you expand it, so even huge dumps open " +
        "fast.</p>" +
        "<p>left-click expands a node; right-click expands or collapses " +
        "the whole subtree under it. arrays and structs fold too.</p>" },

      { id: "mhvalves", title: "mh valves", tab: "mhvalves", body:
        "<p>GM material-handling gripper and valve config, laid out like " +
        "the pendant: pick a tool, get one card per configured valve with " +
        "its clamp setup and its input/output signals resolved to real " +
        "DI/DO — name and number, each linking into the " +
        "<a data-topic='io'>io tab</a>.</p>" +
        "<p>a magnet end-effector (KAREL-driven, not an MH valve) is " +
        "surfaced in its own section, and the full untouched config tree " +
        "stays at the bottom — the evidence is always inspectable.</p>" },

      { id: "photos", title: "photos", tab: "photos", body:
        "<p>saved inspection images from a camera backup: the most recent " +
        "shot big, with its parsed pass/fail report, over a grid of the " +
        "rest — thumbnails load as they scroll into view, so plant-scale " +
        "photo sets stay fast. click any shot for the full-size " +
        "lightbox.</p>" +
        "<p>on a robot with <a data-topic='cameras'>linked cameras</a>, " +
        "the same view shows each linked camera's photos.</p>" },

      { id: "cameras", title: "cameras", body:
        "<p>vision devices are part of the fleet: link a Keyence CV-X or " +
        "Matrox camera to its robot in the " +
        "<a data-topic='library'>library</a>, and it rides along — one " +
        "click backs up the robot and all its cameras together, and the " +
        "robot's <a data-topic='photos'>photos</a> view reads the camera's " +
        "saved images.</p>" +
        "<p>the library's <code>multi-cam</code> lens flips the same " +
        "plant/line folders into a wall of live Matrox tiles; click one " +
        "for the camera's remote operation screen. CV-X units have no " +
        "live frame to tile, so they honestly stay out of the wall — " +
        "their remote screen opens from the robot's camera list " +
        "instead.</p>" },

      { id: "files", title: "files", tab: "files", body:
        "<p>the raw file browser: every file in the backup, with text and " +
        "hex preview. this is the floor under every other tab — when " +
        "nothing else covers a file, this always shows you exactly what's " +
        "on disk.</p>" },

      { id: "themes", title: "themes & backgrounds", body:
        "<p>the 🎨 button (or <kbd>t</kbd>) opens the theme window — " +
        "everything about how the app looks. hover a theme to preview it " +
        "live, click to keep it; <kbd>shift</kbd>+<kbd>t</kbd> cycles " +
        "themes from anywhere. custom themes are yours to build and are " +
        "saved as small files you can share with the next tech.</p>" +
        "<p>the customize tab holds the ui font (mono, ROG, sans, serif), " +
        "text size and chrome scale — data text and window chrome scale " +
        "separately, so big text never squeezes out the tables — plus " +
        "panel borders.</p>" +
        "<p>ambient background effects draw behind the app if you want " +
        "them — every one tints itself from the active theme, with " +
        "intensity and size sliders. they're off by default and " +
        "deliberately cheap, and they pause whenever the window is " +
        "hidden.</p>" },

      { id: "settings", title: "settings", body:
        "<p>⚙ holds app behavior: the 3d view's per-axis rotation invert, " +
        "and the library folder — the one root that is both where FTP " +
        "backups land and what the " +
        "<a data-topic='library'>library</a> shows. changing it rescans.</p>" +
        "<p class='dim'>looks live in <a data-topic='themes'>🎨 themes</a>. " +
        "all preferences are stored per-user in %APPDATA% — never inside " +
        "a backup.</p>" },

      { id: "keyboard", title: "keyboard", body:
        "<p>everything is reachable without the mouse: number keys switch " +
        "tabs, <kbd>/</kbd> focuses a tab's filter, <kbd>j</kbd>/<kbd>k</kbd> " +
        "move through lists, <kbd>enter</kbd> opens, <kbd>esc</kbd> backs " +
        "out, <kbd>backspace</kbd> walks your view history.</p>" +
        "<p>the full map lives in the " +
        "<a data-tab='shortcuts'>shortcuts tab</a> of this window.</p>" },
    ],

    /* the shortcuts tab's rows: [keys, what it does] */
    shortcuts: [
      ["1 – 9 · 0 · - · =", "switch tab (the number row; 0 = 3d view)"],
      ["ctrl+k", "search whole backup"],
      ["backspace", "back (previous program / view)"],
      ["/", "focus tab filter"],
      ["esc", "clear filter · back to list · close"],
      ["j / k or ↓ / ↑", "move selection"],
      ["h / l or ← / →", "switch pane (split views)"],
      ["enter", "open selection · search signal"],
      ["t / shift+t", "theme window / cycle theme"],
      ["?", "help"],
    ],
  };
})();
