# Changelog

All notable changes to devdash-mcp will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `screenshot_capture` / `screenshot_gauge_preview` accept an optional
  ``roi`` ({x, y, width, height}) for post-pipeline cropping, plus
  ``inline_thumbnail`` and ``thumbnail_max_dim`` for opt-in base64 previews.
  Default response is now path-only (`{path, width, height, window_id}`),
  see "Changed (breaking)" below.

### Changed (breaking)

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
