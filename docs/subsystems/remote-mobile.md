# Remote & mobile — the wire protocol, the listening socket, the QR handoff

*Subsystem doc #4. Written 2026-08-03 against `main` @ `6609992`, clean tree
(plus this pass's own two comment corrections in `cvx_remote.py` and
`INVENTORY.md`, §9 item 1 — both 1-for-1 swaps, so cites into those files
hold). Line-number cites are against that revision and drift with edits; the
anchor commit is the reference.*

Covers: src/backupviewer/cvx_remote.py, src/backupviewer/phoneview.py,
src/backupviewer/qr.py, src/backupviewer/screengrab.py,
src/backupviewer/cvx_handshake/chan8502_tx.bin,
src/backupviewer/cvx_handshake/chan8503_tx.bin,
src/backupviewer/cvx_handshake/chan8504_tx.bin,
src/backupviewer/web/js/cvxremote.js,
src/backupviewer/web/js/mtxremote.js,
src/backupviewer/web/js/phoneview.js
(10 files)

> **Template note.** The ten-section shape is kept, **including §6 Failure
> modes**. Doc #3's refined keep-rule — keep §6 when the subsystem must
> degrade honestly under missing or contradictory input — applies here with
> the strongest case yet: this is the only subsystem in the app that both
> *opens* sockets to live equipment and *listens* on one, so it has both
> halves, jobs that die mid-flight and a defined display for every absence.
> §6 is the longest section as a result.
>
> **Evidence tags**, carried from docs #1–#3, plus one addition:
> **`vendor-reflected`** — a value read out of the vendor's own shipped
> metadata rather than inferred from samples. It sits *above* `corpus-measured`
> (it is Keyence's own definition, not a measurement) and is orthogonal to
> `pendant-paired`. It is used exactly once, for `VapiMouseEventId` (§4). The
> rest keep their meanings: **`pendant-paired`** = matched against a real
> controller's own output; **`live-run <date> (recorded)`** = discovered
> against real equipment on a stated date and recorded in code/history;
> **`corpus-measured`** = measured across a set of real samples; **`assumed`**
> = an honest guess the code is built to survive being wrong about. Where a
> fact is enforced by a **test** rather than only remembered in a comment, the
> evidence says so — which here is unusually often (this is the best-covered
> subsystem in the app, §8).
>
> **This pass dialled no hardware.** Every number is a static read or an
> offline test. Facts inherited from the original packet captures are
> `live-run 2026-07-2x (recorded)`, never `re-measured` — see the closing
> section.
>
> **Division of labour.** CLAUDE.md owns the rules; INVENTORY.md owns per-file
> breadth. `api.py`'s endpoint surface (shared/infra's file) is *described and
> cited* here but deliberately **not** claimed in `Covers:` — same treatment
> doc #3 gave `get_dcs_zones`. `parsers/mtx_portal.py` is **parsing.md's**; the
> Matrox remote consumes its scrape, so it is linked, not restated.

---

## 1. What it is

Four ways to put a live camera picture — or a live camera *screen* — in front
of a tech standing at the equipment, none of which exist while the app is only
reading a backup. All four surface through the same two entry points (the
camera-remote button on the photos and files tabs, and the 📱 button in the top
bar), and all four vanish when there is nothing to drive:

1. **The CV-X remote** (`cvx_remote.py` + `cvxremote.js`) — a full
   screen-mirror-plus-mouse remote desktop to a Keyence CV-X controller, spoken
   in the controller's *own* TCP protocol. This is the irreplaceable piece: the
   protocol was reverse-engineered from packet captures of Keyence's Terminal
   software, and **the surviving record of it is this file, its three handshake
   blobs, one test file, and this document — nothing else** (§4, and §7 on the
   vanished reference client).
2. **The Matrox remote** (`mtxremote.js`, backed by `mtx_remote_*` +
   `parsers/mtx_portal.py`) — a Matrox camera is operated through the web page
   it already serves, so "remote" here is that page, embedded as sandboxed
   in-app iframe tabs, with a separate-window fallback when the page refuses
   framing.
3. **The phone view** (`phoneview.py` + `phoneview.js` + `qr.py`) — scan a QR,
   and a phone in your hand shows the camera's live HMI frame, relayed by the
   laptop so the phone never needs a route to the robot network. The app's
   **only listening socket**, and the one place its read-only posture has to be
   argued rather than assumed (§4, §5).
4. **The window viewfinder** (`viewfinder_start` + `screengrab.py`) — the same
   QR handoff, but the phone mirrors whatever one of *our* windows is showing (a
   camera remote, a popped-out backup) by grabbing that window's client area. No
   rectangle to pick; it follows the window.

The subsystem's centre of gravity is the CV-X protocol; the other three are
comparatively ordinary once the trust posture is stated. Everything below
spends its length accordingly.

## 2. The files

Per-file descriptions live in the [INVENTORY map](../INVENTORY.md). The
inventory's "remote/mobile" group is exactly these 10 files / 2,032 lines of
code (plus the three binary blobs), and all 10 were unclaimed before this doc.
Within them:

| layer | files |
|---|---|
| protocol client | `cvx_remote.py` (508 — framing, ctx-echo, handshake replay, JPEG harvest, mouse, the MJPEG server) |
| captured traffic | `cvx_handshake/chan8502_tx.bin` (96 KB), `chan8503_tx.bin`, `chan8504_tx.bin` — the replayed client handshake, one blob per socket (§4, §7) |
| relay + capture | `phoneview.py` (408 — the token-gated share server), `screengrab.py` (159 — BitBlt→PNG of a window), `qr.py` (264 — the hand-rolled encoder) |
| overlays (JS) | `cvxremote.js` (313 — the CV-X screen+mouse overlay), `mtxremote.js` (168 — the Matrox iframe-tab overlay), `phoneview.js` (212 — the QR modal + firewall panel) |

Load order (`index.html:131-133`): `cvxremote.js` → `mtxremote.js` →
`phoneview.js`. All three attach to `window.BV` as IIFEs; `phoneview.js`'s
`BV.openViewfinder` is what the other two (and the top bar) call, so it must
load, but there is no ordering constraint between them beyond that (the calls
are at click time).

**Boundary with neighbours** — linked, never claimed here:

- **`api.py`** contributes the whole endpoint surface: a contiguous block at
  `api.py:2135-2512` (`cvx_remote_*` ×9, `mtx_remote_*` ×2, `phone_view_*` ×6,
  `viewfinder_start`), plus the shared `toggle_fullscreen` at `api.py:625`. That
  is shared/infra's file; §3 walks the surface and cites the lines.
- **`parsers/mtx_portal.py`** (the DesignAssistant operator-page scraper) is
  parsing.md's. `mtx_remote_start` calls `find_da_pages` on the probed HTML; the
  parser facts live there.
- **`tabs/photos.js`, `tabs/files.js`, `tabs/home.js`** are the *entry points* —
  each renders a "remote" affordance that dispatches to one of the overlays.
  They are primary elsewhere (cameras / backup parsing). Mentioned here only as
  callers (and one of them carries a §9 finding).
- **`components/icons.js`** (shared/infra) owns the phone / remote / lock /
  unlock glyphs the bars use; linked only. Its inventory appendix row had a
  stale glyph count, corrected this pass (§9 item 1).

## 3. The flow

Three independent flows share the QR/relay tail. The CV-X one is the deep one:

```
CV-X remote (cvx_remote.py)
  chan850x_tx.bin ─ _patch_addr(ip) ─ parse_messages ─┐   3 sockets: 8502 ctrl/mouse
  (captured client handshake, one blob per socket)     │              8503 aux
                                                        │              8504 video
  start() ─ connect ×3 ─ _reader×3 + _replay ───────────┤
     _replay: global-seq lockstep, ctx patched in       │   readers LEARN each service
     (§4) ── handshake_done                              │   type's ctx, echo it back
                                                         │
  8504 rx ─ _parse_video ─ strip 40B sub-hdr ─ scan FFD8..FFD9 ─ latest_frame
     └ op5/meth5 end-of-frame ─ _send_frame_ack (or frames stop)
                                                         │
  MJPEG server (start_frame_server) ── http://127.0.0.1:PORT/cvx/<sid>
     └── <img> in cvxremote.js ── mouse events ── queue_mouse (seq-ordered) ── send_mouse

Matrox remote (mtxremote.js)
  mtx_remote_start ─ _probe_http ─ X-Frame-Options/CSP? ─┬ embeddable ─ sandboxed iframe tabs
     └ find_da_pages (mtx_portal) ── operator page tabs  └ refused ──── separate app window

phone view / viewfinder (phoneview.py)
  phone_view_start(ip)  ─┐                      PhoneShare: token-gated http on 8756+
  viewfinder_start(win) ─┴─ start_(window_)session ─ lan_urls ─ ranked {ip,url,kind}
     phone GET /v/<token>        ── the page (one <img>)
     phone GET /v/<token>/frame  ── frame_for: single-flight cached fetch ── camera / screengrab
```

The CV-X, Matrox, and phone paths never touch each other's state. The
viewfinder reuses the phone relay wholesale — the only difference is the frame
source (`screengrab.grab_window_png` instead of an HTTP fetch from the camera),
threaded through as a zero-arg `fetch` callable (`phoneview.py:114-131`).

## 4. Domain truths

The CV-X wire protocol is the reason this document exists, and it exists in the
repo in exactly one place — `cvx_remote.py`'s docstring and comments, the three
handshake blobs, and `test_cvx_remote.py`. So it gets a full prose treatment
first, then the tables. If this doc ever has to be cut for length, cut §7 or
§9, never the protocol.

### 4a. The CV-X wire protocol, in full

**The framing.** Every message on every socket is a **32-byte little-endian
header** followed by its body (`parse_messages`, `cvx_remote.py:124-142`):

```
offset  0    4    8     12      16      20  24  28
field   seq  ctx  type  opcode  method  0   0   bodyLen      + body[bodyLen]
```

`type` is the *service* (0x18 login, `7` remote-desktop, `6` video); `opcode`
and `method` name the operation within it; `ctx` is the controller-assigned
context the client must echo. The framing is trivially testable and tested:
`test_parse_messages_roundtrip` builds messages by hand and reads the fields
back, and `test_bundled_handshake_blobs_parse` frames all three shipped blobs to
the byte.

**ctx: learn from replies, echo forever.** The controller assigns a `ctx` value
**per service type** the first time it replies about that service, and every
later client message of that type must carry it. The readers learn it
(`_learn_ctx`, `:283-286`, guarded so a sentinel `0xFFFFFFFF` or an
out-of-range type — e.g. a port number — is never stored); `_prepare`
(`:338-355`) stamps it back in before each replayed message. Two subtleties,
both load-bearing:

- **The video ctx is keyed by service type 6, not by the port number 8504.**
  Frames route to 8504 *because* the type-7 video-route message (`method 0x17`)
  has its body pointed at the type-6 ctx (`_prepare:350-354`), not because of
  anything about the port. Get this wrong and video never routes and every
  frame-ack is stamped with the wrong context. This is what
  `test_prepare_routes_video_using_type6_ctx_not_port` pins, deliberately
  planting a port-keyed red herring (`_ctx[8504]`) that the code must ignore.
- **OPEN and channel-open messages (opcodes 0 and 2) carry no ctx yet** and are
  left unpatched (`_prepare:345`), because the ctx does not exist until the
  controller has replied.

**The handshake replay — lockstep, in global seq order.** `start()` connects
all three sockets, spawns a reader per socket, and spawns one `_replay` thread
(`:217-240`). The captured client handshake (the three `chan850x_tx.bin` blobs)
is concatenated, sorted by `(seq, port)`, and replayed in that single global
order (`_load_handshake:209-215`, `_replay:357-386`). It is **lockstep**, not
fire-and-forget: after an OPEN (`opcode 0`), wait for the op1 ack on that socket
(`:374-375`); after a REQUEST (`opcode 5`), wait for the op6 response
(`:376-377`); otherwise a short paced sleep. Two messages are handled specially:
the type-7/method-0x17 body is pointed at the learned video ctx (above), and the
**8504 `op6/meth5` message is skipped** during replay (`:363-365`) — it is the
frame-ack "prime", sent *reactively* the first time the controller asks, not
replayed. §7's census shows the blob's third 8504 message is exactly `(6,5)`:
the code's skip rule and the captured bytes agree.

**Video: accumulate, strip, scan.** On 8504, `_parse_video` (`:302-334`) walks
the framed messages. Only actual video-data bodies join the image buffer — the
`op7/meth4` chunks and the single `op5/meth5` end-of-frame — and only after each
has its **40-byte sub-header** stripped (`_VIDEO_SUBHDR`, layout fully documented
at `:77-86`). The sub-header's own chunk-length field is **0 on the final
chunk** and cannot be trusted; strip the fixed 40 bytes and let the SOI/EOI scan
(`extract_jpegs:149-167`) trim the trailer. Two defensive rules ride here: a
`body_len > 10_000_000` is treated as desync and the buffer resyncs one byte at
a time (`:306-308`); and every `op5/meth5` triggers a 20-byte frame-ack
(`_send_frame_ack:396-408`) — omit it and the controller stops sending.

**The rendering shortcut.** Decoded frames are not handed to JS to decode.
`start_frame_server` (`:501-508`) runs a tiny localhost HTTP server that streams
the session's latest JPEG as `multipart/x-mixed-replace`, so the frontend is a
plain `<img src="http://127.0.0.1:PORT/cvx/<sid>">` (`cvxremote.js:250`) — the
live screen renders with zero JS decoding, in a `file://` page with no CSP to
fight.

**Mouse, and the drag finding.** Mouse events are 60-byte messages on 8502
(type 7 / op 5 / method 0x34), body `[7, h1, h2, eventId, X, Y, h3]` where
`h1/h2/h3` are three client-side handle constants the controller just echoes
(`send_mouse`, `:410-428`, `_H1/_H2/_H3` at `:57`). Wheel rotation and the
middle button ride the same message with no extra fields. The **drag finding**
is the most expensive field lesson here, and it is worth stating as what it is —
*a correction of a wrong first theory*. Dragging "snapped at release." The first
diagnosis — promise-chain starvation in WebView2 — was **wrong**, and was
disproved by reading pywebview 6.2.1's own source. The real cause: the
controller **ignores plain `MOVE(0)` while a button is held** and pans only on
`DRAGGED=14` / `WHEEL_DRAGGED=15`. Two consequences in the code, both of which
look odd until you know the history:

1. `cvxremote.js` sends the drag id, not MOVE, once a held-button move passes a
   4-screen-pixel dead zone (`cvxremote.js:175-191`; the dead zone also keeps a
   jittered right-click from drag-cancelling the controller's context menu).
2. `queue_mouse` (`cvx_remote.py:430-457`) reorders events by client seq under a
   lock. The reason is **not** delivery stalling — it is that pywebview spawns a
   fresh thread per bridge call, so a press can arrive before its positioning
   move. The frontend fires events without awaiting (chaining on the bridge
   promises is what actually stalls a continuous drag) and stamps each with a
   sequence number; Python restores the order. Sends happen **inside** the lock
   — draining on two threads at once would re-scramble the order the buffer just
   restored — and a hole older than 150 ms is skipped, because "a frozen mouse
   is worse than one lost event" (`:447`).

**Provenance — how to re-derive `VapiMouseEventId` (memory-only).** This lives
nowhere in the repo and is the difference between "unrecoverable" and "an hour's
work," so it belongs in the doc. The Keyence CV-X Terminal / Simulator install
carries the protocol enums as .NET metadata in `Vapi.Net.dll`. Reflect them with
**32-bit** Windows PowerShell — `Assembly.LoadFrom` throws on 64-bit for that
x86 assembly — and drive the reflection script via `-File`, because an inline
`-Command` string eats the `$` variables. That is the whole recipe for
`VapiMouseEventId` (and its sibling `VapiConsoleKeyCode`, §9's closed keyboard
question).

The protocol facts, tagged:

| Fact | Evidence |
|---|---|
| Three TCP sockets — 8502 control/mouse, 8503 aux, 8504 video — every message a 32-byte LE header `[seq, ctx, type, opcode, method, 0, 0, bodyLen]` + body | `cvx_remote.py:46,124-142`; **live-run 2026-07-2x (recorded)**. Test-enforced (`test_parse_messages_roundtrip`, `test_bundled_handshake_blobs_parse`) |
| The controller assigns a `ctx` per service type; the client learns it from replies and echoes it. The **video ctx is keyed by type 6, not port 8504** | `cvx_remote.py:48-51,283-300,338-355`; **live-run (recorded)**. Test-enforced with a port-keyed red herring the code must ignore (`test_prepare_routes_video_using_type6_ctx_not_port`) |
| `VapiMouseEventId`: `0` move · `1/2` L down/up · `3/4` R down/up · `5/6` wheel *button* (middle) · `7/8/9` long-press · `10/11` wheel rotation · `12/13` click/double-click · `14` dragged · `15` wheel-dragged. The app sends 0–6, 10, 11, 14, 15; not 7–9, 12, 13 | **`vendor-reflected`** from `Keyence.Ve.Interop.VapiMouseEventId` in `Vapi.Net.dll` (0–4 also proven live in the capture). Test-enforced against tidying, deliberate gaps and all (`test_mouse_event_ids_match_vendor_enum`) |
| Moving with a button held is **not** a MOVE to the controller — it pans only on `DRAGGED`/`WHEEL_DRAGGED`; a plain MOVE while pressed is ignored and the viewport snaps at release | **live-run (recorded)**; stated in both the Python (`cvx_remote.py:59-66`) and JS (`cvxremote.js:23-32`) comments — the corrected wrong-first-theory (this section; the trap in §7) |
| Video frames are full 1024×768 JPEGs on change; each is `op7/meth4` chunk bodies + one `op5/meth5` end-of-frame, every body led by a **40-byte sub-header** whose own length field is 0 on the final chunk (untrustable — strip the fixed 40, let the SOI/EOI scan trim) | `cvx_remote.py:77-86,302-334`; **live-run (recorded)**. Test-enforced byte-identical across arbitrary recv() splits (`test_parse_video_strips_subheaders_frame_is_byte_identical`, and the zeroed final field is reproduced in the `_subhdr` fixture) |
| Reply to every `op5/meth5` with the 20-byte frame-ack or frames stop | `cvx_remote.py:72-75,318-334`; **live-run (recorded)**. Test-enforced (`test_parse_video_decodes_frame_and_acks_with_type6_ctx`) |

### 4b. The phone relay and its trust posture

| Fact | Evidence |
|---|---|
| `phoneview.py` runs the app's **only listening socket**, narrowed five ways: off by default (the server exists only while a share is active and dies with the app), every route token-gated via `secrets.token_urlsafe`, the camera IP fixed by the desktop user (nothing a phone sends chooses what is fetched), single-flight fetch with a floor, and 404s that reflect nothing back | `phoneview.py:10-20,71,203-221,335-356`; test-enforced across the set (`test_unknown_paths_are_404`, `test_label_is_html_escaped`, `test_frame_cache_rides_one_camera_fetch`, `test_camera_down_serves_stale_frame_with_honest_age`) |
| Gentle with the camera: five phones cost it at most one request per `MIN_FETCH_GAP` (0.45 s) — one thread refreshes while every other rides the cache; the camera's own HMI polls at 1 Hz and the relay never exceeds ~2 Hz | `phoneview.py:39,203-221`; **corpus-measured** rationale, single-flight test-enforced (`test_frame_cache_rides_one_camera_fetch`) |
| The QR dials the most reachable address: the Windows mobile-hotspot net (`192.168.137.*`) first, then other private LANs, with the camera-facing adapter demoted last | `phoneview.py:361-408`; test-enforced ordering (`test_rank_hotspot_first_camera_net_last`) |
| A window session is a singleton that re-points: pressing 📱 in a different window re-targets the one window share (and re-labels it) rather than opening a second | `phoneview.py:114-131`, `api.py:2426-2448`; test-enforced (`test_window_session_is_a_singleton_and_repoints`) |

### 4c. The QR encoder, window capture, the Matrox remote

| Fact | Evidence |
|---|---|
| `qr.py` is hand-rolled because the stack is locked — the canonical instance of CLAUDE.md's "redesign the feature rather than add a dependency": a QR *is* the phone-view URL, so it is 264 lines of stdlib, not a library | `qr.py:1-12`; **the** locked-stack example |
| Deliberately scoped: byte mode, EC level L, versions 1–5, a single Reed-Solomon block with no interleaving — caps payloads at 106 bytes, triple what a LAN URL needs; longer raises | `qr.py:62-67,244-264`; test-enforced limit (`test_version_selection_and_limits`: 106 fits, 107 raises, UTF-8 counts bytes) |
| Verified end-to-end against an **independent decoder** (zxing-cpp) across every version and mask before landing; the offline tests re-prove each piece — RS syndromes zero, BCH minimum distance 7, finder/timing/alignment geometry, and a from-scratch reader that unmasks and re-reads the payload out of the finished matrix | `qr.py:9-11`; the read-back decoder is `test_qr.py:139-213` — "prove against an outside oracle, then pin it offline" |
| `screengrab.py` is BitBlt via ctypes + zlib: the stdlib has no JPEG encoder, but a PNG writer is forty lines (`png_encode`), so capture stays inside the locked stack | `screengrab.py:1-13,37-51`; test-enforced by decoding the encoder's own output by hand (`test_png_roundtrip`) |
| `grab_window_png` re-finds the window by exact title on **every** call, so the mirror follows the window if the tech moves or resizes it; a closed/minimized window raises `OSError` → 503 with an honest reason | `screengrab.py:144-157`, `phoneview.py:349-356`; test-enforced (`test_window_capture_failure_is_reported`) |
| The DPI trap: each capture flips *its own* thread to per-monitor DPI awareness and restores it, so window rects and BitBlt agree on **physical** pixels on scaled displays | `screengrab.py:92-109`; **live-run (recorded)** Windows behaviour, paid for once |
| The pointer-sized-handle trap: the user32 prototypes carry explicit `restype`/`argtypes`, or ctypes truncates a Win64 HWND to 32 bits and the call silently no-ops | `screengrab.py:112-125`; test-enforced (`test_window_handles_are_pointer_sized`) — the "picker never showed" bug (§7) |
| A Matrox camera is operated through the web page it serves on port 80; the probe reports embeddability, and `X-Frame-Options`/`CSP frame-ancestors` refusal falls back to a separate app window. Scraped DesignAssistant operator pages become in-app tabs, so the portal's browser popups are suppressed | `mtxremote.js:1-13,100-166`, `api.py:2298-2344`; test-enforced both branches (`test_start_not_embeddable_on_*`, `test_start_login_page_still_counts_as_up`). The scrape is **parsing.md's** `find_da_pages` |

## 5. Invariants

What must stay true, what enforces it, what breaks if it doesn't.

1. **The video ctx is keyed by service type 6, never the port.** `_prepare`
   routes on `_ctx[VIDEO_TYPE]` (`cvx_remote.py:350-354`); `_learn_ctx` refuses
   to store a type ≥ 1000 (`:284`) so a port number can never masquerade as a
   service type. Test-enforced with a port-keyed red herring
   (`test_cvx_remote.py:185-194`). Break it and video never routes.
2. **The captured address is rewritten, same length, or it raises.**
   `_patch_addr` rewrites the advertised `TCP:<ip>` to the controller actually
   dialled, keeping the exact byte length, and **raises** rather than silently
   replaying the capture-time address if the field can't hold the ip
   (`cvx_remote.py:110-121`). Test-enforced including the too-small case
   (`test_patch_addr_rewrites_advertised_ip_same_length`).
3. **No bundled blob carries a real address** — the plant-identifier firewall
   as a *test*, not a habit, which is the strongest form in the codebase.
   `test_bundled_handshakes_carry_no_real_address` (`test_cvx_remote.py:116-137`)
   asserts every dotted-quad in every blob is an RFC 5737 documentation range
   (`192.0.2.` / `198.51.100.` / `203.0.113.`) — anywhere in the bytes, not just
   the `TCP:` field. Because the blobs are opaque captured traffic nobody reads
   in review, a capture-time plant address would be functionally invisible *and*
   still shipped in a public repo; the test is the only thing standing between
   those two facts. If a re-capture ever fails it: scrub the blob, do not relax
   the test.
4. **The app elevates exactly once, and only here.** For a read-only evidence
   viewer this is the most privileged thing in the codebase, so it is stated
   plainly: nothing in the app elevates except `phone_view_firewall_fix`
   (`api.py:2495-2512`), on explicit user action from the phone-view firewall
   panel, and the elevated payload is one Windows Firewall inbound-allow rule.
   `phone_view_firewall_status` (`:2477-2493`) is a non-elevated read and the
   single source of truth for the copy button. Test-enforced: the launch decodes
   to exactly our rule (`test_firewall_fix_launches_elevated`).
5. **The one remote slot is always released before it is re-taken.** A CV-X has
   exactly one remote slot. Reload hangs up, waits, then redials under the same
   session id (`api.py:2170-2189`); a pop-out *adopts* rather than re-dials
   (`cvx_remote_info`); closing a window or the app stops the session; a failed
   reload rebinds the registry to the new id. All test-enforced (`test_cvx_window.py`).
6. **Only actual video bodies join the frame.** Control traffic on 8504 (op1
   acks, op6 responses) is excluded from the image buffer
   (`cvx_remote.py:320-327`); a frame comes out byte-identical across arbitrary
   recv() splits. Test-enforced (`test_parse_video_ignores_non_video_bodies`,
   `test_parse_video_strips_subheaders_frame_is_byte_identical`).
7. **The listening socket exists only while a share does, and dies
   synchronously.** Off by default, up on the first share, down on the last
   share leaving — with the socket fully released on return so a restart cannot
   race a draining server for the port (`phoneview.py:172-199`). Test-enforced
   (`test_stop_last_session_stops_server`,
   `test_expired_session_is_gone_and_server_stops`).
8. **The phone chooses nothing.** The camera IP is fixed by the desktop user;
   tokens gate every route; unknown paths 404 with no reflection; the phone
   never reaches the camera VLAN, only the laptop (`phoneview.py:10-20`).
   Test-enforced (`test_unknown_paths_are_404`, `test_api_qr_renders_only_active_share_urls`).

## 6. Failure modes

The degradation ladder: what is unreachable, contended, or malformed → what the
code does about it. "Test-enforced" = a unit test or the probe pins it;
"traced" = read off the code this pass, held by nothing.

1. **Controller unreachable, or the slot is taken.** If the CV-X is off, or the
   Keyence Terminal or an operator already holds its one remote slot, the
   connect fails and `start()` returns False with a message naming both causes in
   one line (`cvx_remote.py:228-229`). Test-enforced
   (`test_start_reports_connect_failure`); the UI shows the same two-cause hint
   (`cvxremote.js:261-263`).
2. **A re-captured handshake blob whose address field is too short** → a loud
   `ValueError`, never a silent replay of the capture-time address
   (`cvx_remote.py:110-121`). Test-enforced.
3. **The control socket drops mid-session** → the 8502 reader sets `_alive`
   false on close (`cvx_remote.py:280-281`), the readers unwind, and the status
   poll flips the overlay to "disconnected" (`cvxremote.js:303-304`). Traced.
4. **Video desync** → a `body_len > 10 MB` resyncs the buffer one byte at a time
   rather than trusting the length (`cvx_remote.py:306-308`). Traced.
5. **A mouse sequence hole** → the stream stalls for at most ~150 ms, then the
   next arrival skips past the dead call (`cvx_remote.py:436-457`). Test-enforced
   (`test_queue_mouse_skips_a_dead_hole`).
6. **The Matrox home page refuses embedding** (`X-Frame-Options` / `CSP
   frame-ancestors`) → the overlay closes and re-opens the page in a separate app
   window (`mtxremote.js:136-143`, `api.py:2317`). Test-enforced
   (`test_start_not_embeddable_on_*`).
7. **The mirrored window moves, resizes, or vanishes** → `grab_window_png`
   re-finds it by title every call; closed/minimized raises `OSError`, surfaced
   as a 503 with an honest reason and reflected in the status line
   (`screengrab.py:144-157`, `phoneview.py:349-356`, `phoneview.js:195-198`).
   Test-enforced (`test_window_capture_failure_is_reported`).
8. **The phone can't reach the PC** → two honest surfaces: the status line only
   flips to "watching" when a phone *actually* pulls a frame within 15 s
   (`phoneview.js:190-193`), and the corner "?" opens the firewall panel (§5,
   §7) for the "server stopped responding" case. Traced; the firewall endpoints
   are test-enforced.
9. **The camera is down but a frame is cached** → the relay serves the stale
   frame with an honest `X-Frame-Age` and records `fetch_err`, rather than
   blanking (`phoneview.py:203-221`). Test-enforced
   (`test_camera_down_serves_stale_frame_with_honest_age`); with no cache it is a
   clean 503 (`test_camera_down_no_cache_is_503`).
10. **The last share ages out or is stopped** → the listening socket is released
    synchronously, so a restart can never race a draining server for the port and
    a forgotten app instance goes fully quiet (`phoneview.py:158-199`).
    Test-enforced.
11. **A pop-out whose reload failed** re-dials under a **new** session id; the
    window registry and fullscreen state rebind to it, or closing the window
    would strand the camera's one remote slot until app exit (`api.py:2242-2257`,
    `cvxremote.js:243-247`). Test-enforced
    (`test_failed_reload_rebind_stops_the_redialed_session`).
12. **Probe / headless environment** → the CV-X overlay runs in a hidden
    WebView2 with `CvxRemoteSession` faked (nothing dials a camera); the bar
    shape, the reload-keeps-the-id rule, fullscreen-through-the-window, and the
    pop-out adopt are all asserted on real DOM (`ui_cvxremote_probe.py`).

## 7. Traps paid for

- **Appending 8504 control traffic to the frame — the patchy artifacting.** The
  op1 acks and op6 responses on the video socket carry no image bytes; a frame
  that included them decoded as garbage patches until the next restart marker
  (`cvx_remote.py:320-323`). Now only `op7/meth4` and `op5/meth5` bodies join the
  buffer, and a control body carrying accidental JPEG markers is refused as a
  phantom frame (`test_parse_video_ignores_non_video_bodies`). The blob census
  makes the discipline concrete: run the three blobs through the module's own
  `parse_messages` and you get —

  | blob | messages | `(opcode, method)` sequence |
  |---|---:|---|
  | 8502 | 13 | `(2,-)` `(0,-)` `(5,46)` `(0,-)` `(5,21)` `(5,23)` `(7,26)` then six × `(5,21)` |
  | 8503 | 1 | `(2,-)` — the single channel-open |
  | 8504 | 3 | `(2,-)` `(0,-)` `(6,5)` |

  (`-` = method `0xFFFFFFFF`.) Six of 8502's thirteen messages are ~16 KB each
  (98,352 of the file's 98,808 bytes; the other seven total 456), confirming the
  inventory row's "six 16 kb blobs." And 8504's third message is `(6,5)` —
  exactly the one `_replay` skips (`:363-365`) as the reactive frame-ack prime.
  The code's skip rule and the captured bytes agree.

- **The drag that snapped at release — a wrong first theory.** Diagnosed first
  as promise-chain starvation in WebView2; that was disproved by reading
  pywebview 6.2.1's source, and the real cause was the controller ignoring
  plain MOVE while a button is held (§4a). The cost was a rewrite of the mouse
  path around the true cause (drag ids past a dead zone, seq-ordered sends under
  a lock), which is why `queue_mouse` looks the way it does.

- **A Win64 HWND truncated to 32 bits — "the picker never showed."** Without
  explicit `restype`/`argtypes`, ctypes treats a top-level HWND as a 32-bit int,
  truncates it, and the call silently no-ops (it made `SetWindowPos` a no-op once).
  Every user32 call in `screengrab.py` now carries pointer-sized handle types on
  an isolated `WinDLL` handle (`screengrab.py:112-125`), guarded by
  `test_window_handles_are_pointer_sized`.

- **A Public-only firewall rule on a Private hotspot.** The firewall fix adds the
  inbound-allow rule for **all** profiles (`-Profile Any`), because a rule scoped
  to Public is exactly what left a phone blocked when the Windows mobile hotspot
  came up as a Private network (`api.py:2473-2475`,
  `test_firewall_command_shape`).

- **A hardcoded window title that went stale.** `viewfinder` looks up the main
  window by its *live* title, not a constant, because `FindWindowW` is
  exact-match and a hardcoded copy already broke the main-window mirror once —
  silently — when the v1.4 branding rename changed the title
  (`api.py:2411-2417`).

- **SO_REUSEADDR would let two app instances fight for the phones.** The share
  server sets `allow_reuse_address = False`, so a second instance can't silently
  double-bind a port that is already listening; it moves up to the next free port
  instead (`phoneview.py:51-56`, `test_port_conflict_moves_up`). The tests
  themselves live in their own port range for the mirror-image reason — a Windows
  wildcard listener shadows a closed loopback bind, so a stopped test server would
  otherwise look alive (`test_phone_view.py:18-23`).

## 8. Coverage

Counted 2026-08-03. Full-suite anchor: `python -m pytest tests -m
"probe or not probe"` → **701 passed, 0 skipped** (the number every subsystem
doc re-runs; see the report at the end).

**145 unit tests across seven files** — verified by collection this pass, each
count checked individually:

| file | tests |
|---|---:|
| `tests/test_qr.py` | 34 |
| `tests/test_phone_view.py` | 25 |
| `tests/test_cvx_remote.py` | 23 |
| `tests/test_mtx_remote.py` | 21 |
| `tests/test_cvx_window.py` | 19 |
| `tests/test_viewfinder.py` | 16 |
| `tests/test_screengrab.py` | 7 |

Plus `tests/ui_cvxremote_probe.py` (200 lines), which **is** in
`test_probes.py`'s explicit `PROBES` list (`test_probes.py:54`), so it actually
runs under the probe suite — not one of the untracked, hand-run-only probes.
All of it is offline by construction: `CvxRemoteSession._connect` is injectable
and the probe fakes the whole session; `test_phone_view` and `test_viewfinder`
drive **real loopback HTTP** with the camera fetch / window grab faked;
`test_mtx_remote` fakes the HTTP probe and stubs `webview`.

**This is the best-covered subsystem in the app** — the opposite of doc #3's
finding, and worth stating as measured fact. Tests per 100 covered lines,
computed this pass across the four subsystem docs:

| subsystem doc | tests | covered lines | tests / 100 lines |
|---|---:|---:|---:|
| **remote/mobile** | **145** | **2,032** | **7.14** |
| backup capture | 58 | 2,269 | 2.56 |
| parsing | 122 | 5,045 | 2.42 |
| 3D viewer | 28 | 3,104 | 0.90 |

remote/mobile leads on both the absolute count (145 is the most of any subsystem
doc) and the density (nearly 3× the next). The likely *why* transfers: this is
the one subsystem that could never be tested by hand against real equipment —
one session per controller, and an operator may be on it — so the offline fakes
were built out of necessity, first, instead of as an afterthought. If that
reading holds, it is the lesson the batch-backup coverage gap in the backlog
(the next session's work) most needs.

**The DOM-level gap.** The probe is CV-X-only: it drives `.cvx-remote` /
`.cvx-bar` and the top-bar 📱 button, asserting the bar shape, the
reload-keeps-the-id rule, fullscreen-through-the-window, and the pop-out adopt —
but there is **no probe that opens the QR modal or a Matrox iframe tab**
(grep-verified this pass: the only phone assertion is that the top-bar button
*calls* `openViewfinder`, stubbed). So `phoneview.js`'s modal DOM and
`mtxremote.js`'s tab strip are untested at the DOM level; their *backends*
(`phoneview.py`, `mtx_remote_*`, `qr.py`) are the most-tested code in the app,
and the relay's HTTP surface is covered thoroughly over real loopback, not
mocked.

## 9. Open questions

Found during this pass; recorded, not fixed (ground rule: no feature code
changes). Evidence attached.

1. **CORRECTED THIS PASS — two stale references, both 1-for-1 swaps** (the only
   file touches this pass makes, so all cites above hold):
   - `cvx_remote.py:3-4` cited `CvxRemote/mirror2.cs` and `CVX_REMOTE_HANDOFF.md`
     as the reference client. Neither exists (§4a, §7) and it was already on
     `INVENTORY.md`'s stale-docstring list. Replaced with a pointer to this doc
     and a plain statement that the reference client is gone and the surviving
     record is here.
   - `INVENTORY.md`'s dual-membership appendix described `components/icons.js` as
     "4 inline stroke-svg glyphs (phone/gear/help/remote)". The file exports
     **6** — `phone`, `gear`, `help`, `lock`, `unlock`, `remote` — and the
     inventory's *main* row already said 6. The appendix row was stale; corrected.
2. **`BV.openPhoneView` (`phoneview.js:37`) has no callers.** Every live phone
   button goes through `BV.openViewfinder` — the top bar (`router.js:404`), the
   CV-X bar (`cvxremote.js:123`), the Matrox bar (`mtxremote.js:99`). The
   camera-direct variant is kept "for callers that want it," but there are none
   (grep-verified: only the definition and its own docstring reference the name).
   Already recorded at `INVENTORY.md` §D; cited, not re-discovered.
3. **The home-screen camera tile always opens the Matrox remote.** `home.js`'s
   `camTile` click handler calls `BV.openMtxRemote(ip, …)` unconditionally
   (`home.js:1100-1102`), with no `device_type === "camera-keyence"` branch —
   even though `home.js` knows that device type in four other places
   (`:705,1736,1817,2201`). The photos and files tabs both branch
   (`photos.js:495-504`, `files.js:60-61`: `isCvx ? openCvxRemote : openMtxRemote`).
   So a CV-X camera surfaced as a home tile would open the Matrox web-UI overlay —
   which cannot connect to a CV-X (no web UI on port 80) — instead of the screen
   mirror. Worth confirming whether a `camera-keyence` entry can reach the tile
   path (the tile's `c` object carries `device_type`), then giving `camTile` the
   same branch. Found this pass.
4. **`-EncodedCommand` in the UAC prompt.** The elevated firewall payload is
   base64, so a user granting admin cannot read it in the Windows prompt (§5
   invariant 4, §7). The answer may well be "acceptable, because the plain-text
   command is shown in the panel first" (`phoneview.js:80-93`) — but the doc asks
   rather than assumes. If the answer is no, the shape is a readable `-Command`
   with the rule inline.
5. **The 8504 *rx* claim is prose only.** The comment describes the received
   video stream as "13 op7/meth4 chunks + 1 op5/meth5" (`cvx_remote.py:84-85`) —
   but there is no *rx* blob in the repo, only the three *tx* handshakes, so
   nothing in-tree can confirm it. It stands on the vanished capture's record
   (`live-run 2026-07-2x (recorded)`), and this doc says so rather than implying
   it was checked. (The *tx* blobs *were* counted — §7.)

---

## What this pass could not verify

The honest tail, per the template. No hardware was dialled; everything below is
recorded-and-consistent, not re-proven.

- **The whole CV-X protocol's ground truth** — the socket/type/opcode meanings,
  the ctx-echo rule, the sub-header layout, the frame-ack requirement — is
  inherited from the original packet captures and their now-**vanished** C#
  reference client (`CvxRemote/mirror2.cs` / `CVX_REMOTE_HANDOFF.md`, confirmed
  this pass to be absent from disk, from git history, and from
  `.git/info/exclude`). It is `live-run 2026-07-2x (recorded)`, consistent with
  the shipped blobs and pinned by the offline tests, but not re-provable without
  a live CV-X, the Keyence Terminal, and a fresh capture.
- **The 8504 *rx* stream** ("13 op7/meth4 chunks + 1 op5/meth5") has no blob in
  the repo — only the three *tx* handshakes exist — so it rests on the comment
  and the vanished capture alone (§9 item 5). The *tx* census in §7 *was*
  measured.
- **`VapiMouseEventId` as `vendor-reflected`** — the values are Keyence's own,
  reflected from `Vapi.Net.dll`, and pinned by a test so they cannot drift; but
  this pass did not re-run the reflection (it needs a CV-X Terminal install). The
  32-bit-PowerShell method to redo it is recorded in §4a and exists nowhere else
  in the repo.
- **The dropped-keyboard decision** (owner's call, 2026-07-23) lives only in
  session memory: the CV-X has no PC keyboard, its `remoteControl(keycode,
  subKeycode, count)` drives the *physical* console, and `VapiConsoleKeyCode
  KEY_0..KEY_8` are button **indices**, not ASCII digits — so real value entry is
  already a mouse job on the on-screen keypad. The scaffolding was pursued and
  stripped back out; the one keeper is the tab-guard in `cvxremote.js`
  (`NAV_KEYS`, `onKeyCapture`, `:36,151-156`) that swallows number / `-` / `=`
  keys while the fullscreen remote is up, so they don't switch the tab hidden
  behind it. Recorded here because no repo file explains *why* that guard exists,
  and a future reader would otherwise re-litigate the keyboard question — treat
  it as closed.
- **Every rate and single-flight claim** for the phone relay is proven over
  loopback with the camera faked; the "camera's HMI polls at 1 Hz, we never
  exceed 2 Hz" bound is the docstring's `corpus-measured` figure, not re-measured
  against a real camera this pass.
- **Nothing was run against a real CV-X or Matrox camera.** Every number in this
  doc is a static read or an offline test.
