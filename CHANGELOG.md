# Changelog

All notable changes to devdash-mcp will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`qml_explorer_measure_frame_time` and a `render_timing` flag on
  `qml_explorer_launch`.** A relaunch with `render_timing=True` adds
  `QSG_RENDER_TIMING=1` plus a `qt.scenegraph.time.*=true` rule to the
  explorer's environment, so every frame of Qt's threaded renderloop
  appears in the captured stderr log as
  `frame rendered in Nms, ..., perWindowFrameDelta=D`. The measurement
  tool then samples that log over a configurable window and returns
  aggregate stats — render-cost mean/p50/p95/max/stddev, frame-interval
  same, and the fraction of intervals that hit a 60 fps budget. Render
  costs are integer-millisecond from Qt itself; the tool is best for
  spotting whole-millisecond regressions, not for sub-ms tuning. Useful
  for comparing alternative implementations (e.g. 2D vs Qt-Quick-3D
  components) on the same hardware.

### Fixed

- **`qml_explorer_status` now flags an explorer this session didn't launch.**
  It already verifies liveness actively (`pgrep` + a WebSocket round-trip), so a
  leftover instance from a prior MCP session is correctly `running: true` — but
  callers couldn't tell it apart from one this server manages, and would then
  hit "logs unavailable" / a silent attach on the next launch. The status
  response gains `managed_by_session` (bool / `None` when nothing's running),
  `session_pids`, `foreign_pids`, and — when the only running explorer is a
  foreign one — a `hint` to kill-then-relaunch.

- **`qml_explorer_logs_get` was always empty — the explorer's output went to
  the journal, not the captured file.** `qml_explorer_launch` redirects the
  explorer's stdout/stderr to a per-PID log file, but Qt's default message
  handler only writes to stderr when stderr is a *tty*; redirected to a file,
  it routes `qDebug`/`qInfo`/`qWarning` (and QML `console.log`) to the systemd
  journal instead, leaving the captured file 0 bytes. Launch now sets
  `QT_FORCE_STDERR_LOGGING=1` in the explorer's environment so all of it lands
  in the captured stream. (The explorer itself only logs a couple of `qInfo`
  lines today — a fuller logging story is a qml-gauges-side follow-up — but any
  `console.log` an explorer page emits is now captured.)

- **Screenshot capture re-reads the window's geometry after focusing.**
  `screenshot_capture` / `screenshot_gauge_preview` measured the target
  window's position+size up front and then captured *after* focusing it — but
  focusing a tiled window can move/resize it (a layout reflow, or switching to
  its workspace), so `grim`'s crop region could be stale and bleed in a
  neighbouring window or the wallpaper. The geometry is now re-read after the
  focus settle, just before the capture. (The captured window can still be a
  *different size on different calls* — a tiling compositor sizes the explorer
  from its layout, not from the explorer's `width:` — so the tool docstrings
  now say: derive `roi` coordinates from the `width`/`height` of the previous
  capture, don't hardcode them.)

- **`qml_explorer_set_property` no longer miscoerces string values.** A string
  argument like `"false"` reached the QML side, where `target[name] = value`
  goes through the JS engine and *any* non-empty string is truthy — so
  `set_property("hasFormShading", "false")` actually set the bool to `true`.
  The tool now looks up the property's declared type from the page metadata and
  coerces string inputs accordingly: `"true"` / `"false"` (case-insensitive)
  become real booleans, numeric strings become numbers. A string that can't
  represent the declared type (`"yes"` for a bool, `"x"` for a real, `"1.5"`
  for an int, `NaN`/`inf`) is rejected with an explicit error and nothing is
  sent to the explorer. JSON values of the natural type (real bools, numbers)
  still pass straight through. When a string was coerced, the response carries
  `coerced_value` / `original_value`.

- **Screenshot tools focus the target window before capturing.**
  `screenshot_capture` and `screenshot_gauge_preview` previously captured the
  window in whatever focus state it happened to be in. On Wayland a compositor
  applies effects to *unfocused* windows — Hyprland's `inactive_opacity` plus
  blur with `xray = true` makes the window translucent and bleeds the wallpaper
  through (and a window on an inactive workspace isn't composited at all), so a
  capture of the unfocused explorer looked brighter / more colourful than the
  user sees in direct view. The tools now focus the target window (Hyprland:
  `hyprctl dispatch focuswindow`; X11: `wmctrl`/`xdotool`), wait briefly for
  the focus-in transition, capture, then restore the previously-focused
  window. The result gains a `focused: bool` field (and a `focus_warning`
  string when focusing failed and the image may still be distorted). A
  not-found target window still returns `{error}` rather than capturing the
  screen region blindly.

- **`qml_explorer_navigate` discovers valid pages dynamically.** The set of
  valid page names was hardcoded in the server (`EXPLORER_PAGES`) and went
  stale until the MCP server was restarted whenever an explorer page was
  added. The tool now derives the list at call time, in priority order: the
  local checkout's `explorer/qml/pages/*Page.qml` files; failing that, the
  running explorer's `getState` `data.pages` array (a no-op until qml-gauges
  adds that field — see the TODO in `src/tools/explorer.py` — but the only
  source that works against a remote explorer with no local source tree);
  failing that, the old hardcoded list. It also confirms the navigation
  actually took (via `getState`) and reports a failure — rather than a false
  success — when the explorer stays on a different page (e.g. a page file that
  exists but isn't wired into `Main.qml`'s `pageIndexMap`). On success the
  response includes `pages` (the current valid set).

- `devdash_logs_get` clamps `count` to at least 1 (it already capped at 1000).

### Added

- `qml_explorer_reset_property` — restores a property's QML binding after
  `qml_explorer_set_property` / `qml_explorer_freeze_property` pinned it to a
  literal (the clean "unfreeze" that previously meant restarting the explorer).
  Sends a `resetProperty` WebSocket action; current explorer builds don't
  implement it, so the tool returns `explorer_support: false` with a pointer to
  the qml-gauges-side change needed, and works unchanged once that lands.

- `screenshot_capture` / `screenshot_gauge_preview` accept an optional
  ``roi`` ({x, y, width, height}) for post-pipeline cropping, plus
  ``inline_thumbnail`` and ``thumbnail_max_dim`` for opt-in base64 previews.
  Default response is now path-only (`{path, width, height, window_id}`),
  see "Changed (breaking)" below.

- `tests/` — a stdlib `unittest` suite (no extra deps; `python -m unittest
  discover` from the repo root). Covers the property-value coercion rules and
  the screenshot focus/restore sequencing, plus end-to-end coverage of the
  explorer tools against an in-process fake WebSocket state server
  (`tests/_fake_explorer.py`) and of the screenshot tools with `hyprctl`/`grim`
  faked — including the type-coercion assertion that a string `"false"` reaches
  the explorer as a JSON `false`, and the focus → settle → capture → restore
  ordering.

- `.github/workflows/tests.yml` — runs the `unittest` suite on push / PR
  against Python 3.10 and 3.12.

### Changed (breaking)

- **`devdash_telemetry_screenshot` returns a path, not inline base64.** It
  previously returned `{image: <base64 PNG>, mime_type, source}` — a full-size
  PNG inline (~25k tokens per call). It now writes the PNG to
  `/tmp/devdash-mcp-screenshots/` and returns `{path, width, height, source}`,
  matching `screenshot_capture`. Pixel data is opt-in via `inline_thumbnail=True`
  (adds `thumbnail_base64` / `thumbnail_width` / `thumbnail_height` /
  `thumbnail_mime_type`). Migration: read `result["path"]` (and feed it to the
  image tools) or pass `inline_thumbnail=True` and read `result["thumbnail_base64"]`.

- **`qml_explorer_get_property` response shape.** Previously returned the raw
  WebSocket response from the explorer (`{success, data: {name, value}}` on hit,
  `{success: false, error}` on miss). Now returns
  `{success, value, is_bound, binding_source}` on success and
  `{success: false, error}` on failure. The tool also now resolves
  animation- and expression-bound properties by falling back to page state
  (previously these returned "not found").

  Migration: callers that read `result["data"]["value"]` should now read
  `result["value"]` and may consult `result["is_bound"]` to know whether the
  value was resolved via a binding. `binding_source` is reserved for a future
  explorer-protocol enhancement that exposes binding expressions; currently
  always `None`.

- **Screenshot tools default to path-only returns.** `screenshot_capture` and
  `screenshot_gauge_preview` previously returned `{image: <base64>,
  mime_type, window_id}`. They now return `{path, width, height, window_id}`,
  writing the PNG to `/tmp/devdash-mcp-screenshots/`. Pixel data is opt-in
  via `inline_thumbnail=True` (adds `thumbnail_base64`, `thumbnail_width`,
  `thumbnail_height` to the response). Rationale: a full-size base64 PNG
  costs ~25k tokens per call; agents should consume the path with image
  tools (image_compare, image_structural_diff, etc.) and only opt into
  pixel data when surfacing results to a human.

  Migration: callers that read `result["image"]` should either (a) read
  `result["path"]` and let downstream tooling load the PNG, or (b) pass
  `inline_thumbnail=True` and read `result["thumbnail_base64"]`.

### Documentation

- Clarified `qml_explorer_get_property`'s `is_bound` semantics (tool docstring
  and `docs/TOOL_GUIDANCE.md`): `is_bound: False` does not prove a property is
  unbound — it only means the value came from the explorer's direct
  `getProperty` lookup rather than the `getState` fallback. The explorer's
  PropertyPanel publishes resolved values for *all* panel properties, so a
  theme-bound property such as `faceColor` reports `is_bound: False`.
