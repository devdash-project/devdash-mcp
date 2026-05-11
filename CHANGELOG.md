# Changelog

All notable changes to devdash-mcp will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
