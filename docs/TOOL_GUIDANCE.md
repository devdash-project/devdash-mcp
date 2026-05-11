# devdash-mcp tool guidance

A "which tool when" reference for working with the DevDash MCP server.
Tool argument schemas are in each tool's docstring; this document is
about *choice* — which tool answers which question, and the methodology
patterns that catch the common verification mistakes.

## Question → tool

| Question | Tool | Notes |
|----------|------|-------|
| "Did this render at all?" | `image_bounding_box` | Check `coverage_ratio > 0.05` (or higher). Cheap binary check on rendered output. |
| "Are these two images globally similar?" | `image_compare` | Returns SSIM (0-1, structural similarity) and mean per-pixel RGB diff. Good for whole-image comparisons. |
| "Did a specific small feature change?" | `image_structural_diff` | Localises the change — count of pixels above a channel-sum threshold, diff bbox, centroid, mean diff inside the bbox. Use this for primitive-level verification where the feature is < 1% of the canvas. |
| "Are these images visually equivalent at a glance?" | `image_perceptual_hash` | 64-bit dHash. **Ineffective for sub-2% feature changes** — dHash downsamples to 9×8 grayscale. For small features, use `image_structural_diff`. |
| "What dominant colors are in this image?" | `image_color_histogram` | Quantized RGB histogram. Background pixels excluded by default. |
| "What is this property's value, including when bound to an animation?" | `qml_explorer_get_property` | Returns `{success, value, is_bound, binding_source}`. Falls back to page state when the explorer's direct getProperty reports "not found" (which it does for bound properties). |
| "This page binds properties to animations and is contaminating my pairwise comparison." | `qml_explorer_freeze_all_properties` | Reads each property's current resolved value and re-sets it as a plain value, breaking the binding. Call before pairwise property-comparison work. |
| "I only want to freeze one specific property." | `qml_explorer_freeze_property` | Same as above but for a single property name. Returns `{success, frozen_value, was_bound}`. |
| "What did the explorer print to stdout/stderr?" | `qml_explorer_logs_get` | Returns the tail of the explorer's captured log file. **Requires the explorer to have been launched by this MCP session**, otherwise returns an explicit error. Relaunch via `qml_explorer_kill` + `qml_explorer_launch` if you started the explorer manually. |
| "Capture the explorer's gauge preview." | `screenshot_gauge_preview` | Returns `{path, width, height, focused, ...}` by default. Pass `inline_thumbnail=True` only when surfacing pixels to a human. Focuses the target window before capturing (then restores focus) so the image matches the user's view — see point 7 below. |
| "Capture any window, on either X11 or Wayland." | `screenshot_capture` | Session type is auto-detected from `XDG_SESSION_TYPE`. Hyprland uses `hyprctl` + `grim`; X11 uses `wmctrl` + ImageMagick. Focuses the target first; result has `focused: bool` (+ `focus_warning` when it couldn't). |
| "Set a property — and have the value land as the right type." | `qml_explorer_set_property` | Coerces string args to the property's declared type: `"true"`/`"false"` (case-insensitive) → real booleans, numeric strings → numbers. A string that can't represent the type (`"yes"` for a bool, `NaN`, `"1.5"` for an int) is rejected — nothing is sent. Prefer passing JSON `true`/`false`/numbers directly. When a string was coerced the response includes `coerced_value`/`original_value`. Still read back with `get_property` (point 3) — coercion fixes typing, not range clamping. |
| "Switch the explorer to a component page." | `qml_explorer_navigate` | Valid page names are discovered from `explorer/qml/pages/*Page.qml` at call time (a newly added page works without an MCP restart) and an unknown name is rejected with the current list. It also confirms via `getState` that the page actually switched — if it didn't (e.g. the page file exists but isn't in `Main.qml`'s `pageIndexMap`) you get `success:false` with `current_page`, not a false success. Success returns `pages` (the valid set). |
| "What's the current state of the explorer?" | `qml_explorer_get_state` | Returns page name, property values (user-set overrides), and property metadata. |
| "What's running and on what fd?" | `qml_explorer_status` | Process IDs + WebSocket reachability. |

## Verification methodology

Patterns that catch common mistakes:

### 1. Default to path-only screenshots

Screenshots are saved to `/tmp/devdash-mcp-screenshots/` and the response
returns `{path, width, height, window_id, focused}`. Pass the path to image
tools for analysis. Only opt into pixel data (`inline_thumbnail=True`) when
surfacing a preview to a human reader — base64 PNGs in tool output cost
roughly 25k tokens per call.

```python
# Programmatic verification:
r = screenshot_gauge_preview()
image_structural_diff(r["path"], reference_path)

# Surfacing to a human:
r = screenshot_gauge_preview(inline_thumbnail=True, thumbnail_max_dim=300)
# display r["thumbnail_base64"]
```

### 2. When suspicious that two operations produced identical results, set an obviously-different intermediate value and re-verify

Two captures with identical perceptual hashes can mean either (a) the
property didn't take effect, or (b) the change is too small for the
metric. Disambiguate by changing the property to something *obviously*
different in between:

```python
set_property("color", "#000000")  # expected change
screenshot -> capture_a
set_property("color", "#ff00ff")  # obviously different intermediate
screenshot -> capture_intermediate    # confirms write+render path works
set_property("color", "#000000")  # back to the test value
screenshot -> capture_b
image_compare(capture_a, capture_b)   # should now be ~identical
```

If `capture_a` and `capture_intermediate` still match, the property is
not taking effect at all.

### 3. Read properties back after setting them

`set_property` returns the explorer's event echo, not a confirmation
that the value passed validation. Some properties silently clamp or
reject out-of-range values. Always verify with `get_property` if the
test depends on the value being applied.

### 4. Freeze animations before pairwise property comparison

Pages like `GaugeTickPage` bind properties to a running animation
(`angle = -135 + animationValue * 2.7`). Two screenshots taken at
different moments capture the tick at different rotational positions,
contaminating any pixel diff with motion noise.

Before comparing screenshots across property settings on the same page,
call `qml_explorer_freeze_all_properties()` once. It re-sets every
property on the current page to its current value, breaking the
underlying binding. From then on, only properties you explicitly change
will change.

### 5. Pick the right diff tool for the feature size

- **Whole-image structural changes** (different gauge altogether,
  different layout) → `image_compare` (SSIM is most sensitive here).
- **Small primitive changes** (a 10×40 px tick reshaped) →
  `image_structural_diff` with `channel_diff_threshold=5`. The diff
  count, bbox geometry, and mean-in-bbox numbers are diagnostic where
  global SSIM and dHash both report ~no change.
- **"Did anything change at all?"** → `image_compare`'s `mean_pixel_diff`
  > some-threshold is a quick yes/no.

### 6. Don't trust dHash for small features

`image_perceptual_hash` downsamples to a 9×8 grid. A 10×40 px tick on
an 800×1000 canvas is below the resolving threshold, and all five
GaugeTick.tickShape variants currently produce the same 64-bit hash.
Use `image_structural_diff` instead for primitive-level work.

### 7. Screenshots match the user's view (focus-before-capture)

`screenshot_capture` / `screenshot_gauge_preview` focus the target window
before grabbing pixels and restore the previously-focused window afterwards.
This exists because compositors style *unfocused* windows differently — on
Hyprland, `inactive_opacity` plus blur with `xray = true` makes the window
translucent and bleeds the wallpaper through, and a window on an inactive
workspace isn't composited at all. Without the focus step, agent screenshots
systematically disagreed with what the user sees in direct view (brighter,
more colourful, washed out).

Implications:

- Captures briefly flip the active window/workspace. Harmless for a dev tool,
  but don't be surprised by the flicker.
- Check `focused` in the result. If it's `false` (with a `focus_warning`),
  the image may still carry compositor artifacts — install the focus helpers
  (`hyprctl` on Wayland; `wmctrl`/`xdotool` on X11) and retry.
- When comparing a fresh capture against an older reference image, make sure
  the reference was also taken focused — a pre-fix reference will show a large
  spurious diff against a post-fix capture.
