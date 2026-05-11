"""Image introspection tools - Pixel-level queries on screenshot PNGs.

These tools let an agent verify what was actually rendered after using the
screenshot tools. They operate on PNG files on disk and return plain
JSON-serializable dicts.
"""

from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP
from PIL import Image
from skimage.metrics import structural_similarity as ssim


_ALPHA_THRESHOLD_DEFAULT = 0.05
_BG_TOLERANCE = 8  # 0-255 channel tolerance when inferring background color


def _load_rgba(png_path: str) -> np.ndarray:
    """Load a PNG and return an (H, W, 4) uint8 array in RGBA order."""
    path = Path(png_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {png_path}")
    img = Image.open(path).convert("RGBA")
    return np.array(img, dtype=np.uint8)


def _foreground_mask(arr: np.ndarray, alpha_threshold: float) -> np.ndarray:
    """Return a boolean mask of foreground pixels.

    Uses the alpha channel if any pixel is not fully opaque. Otherwise falls
    back to "not near the dominant edge color" (treats borders as background).
    """
    alpha = arr[:, :, 3]
    if (alpha < 255).any():
        cutoff = int(round(alpha_threshold * 255))
        return alpha > cutoff

    # No alpha info — infer background color from image edges.
    h, w = arr.shape[:2]
    edges = np.concatenate(
        [
            arr[0, :, :3].reshape(-1, 3),
            arr[h - 1, :, :3].reshape(-1, 3),
            arr[:, 0, :3].reshape(-1, 3),
            arr[:, w - 1, :3].reshape(-1, 3),
        ],
        axis=0,
    )
    bg = np.median(edges, axis=0).astype(np.int16)
    rgb = arr[:, :, :3].astype(np.int16)
    diff = np.abs(rgb - bg).max(axis=2)
    return diff > _BG_TOLERANCE


def _dhash(arr: np.ndarray) -> int:
    """Difference hash: 64-bit perceptual hash."""
    img = Image.fromarray(arr[:, :, :3]).convert("L").resize((9, 8), Image.LANCZOS)
    pixels = np.array(img, dtype=np.int16)
    diff = pixels[:, 1:] > pixels[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bit)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def register_image_tools(mcp: FastMCP) -> None:
    """Register image introspection tools with the MCP server."""

    @mcp.tool()
    def image_bounding_box(
        png_path: str,
        alpha_threshold: float = _ALPHA_THRESHOLD_DEFAULT,
    ) -> dict[str, Any]:
        """Compute the bounding box of non-empty pixels in a PNG.

        For images with an alpha channel, "non-empty" means alpha greater than
        alpha_threshold (0.0-1.0, default 0.05). For images without alpha, the
        dominant edge color is treated as background and the bounding box
        covers everything outside that color.

        Args:
            png_path: Absolute path to a PNG file.
            alpha_threshold: Alpha cutoff in 0.0-1.0 range (default 0.05).

        Returns:
            {x, y, width, height, coverage_ratio, image_width, image_height}
            where coverage_ratio is the fraction of total pixels classified as
            foreground. If no foreground pixels are found, all box fields are
            zero and coverage_ratio is 0.0.
        """
        try:
            arr = _load_rgba(png_path)
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to load image: {e}"}

        h, w = arr.shape[:2]
        mask = _foreground_mask(arr, alpha_threshold)
        coverage = float(mask.sum()) / float(h * w)

        if not mask.any():
            return {
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
                "coverage_ratio": 0.0,
                "image_width": w,
                "image_height": h,
            }

        ys, xs = np.where(mask)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        return {
            "x": x0,
            "y": y0,
            "width": x1 - x0 + 1,
            "height": y1 - y0 + 1,
            "coverage_ratio": round(coverage, 6),
            "image_width": w,
            "image_height": h,
        }

    @mcp.tool()
    def image_color_histogram(
        png_path: str,
        bins: int = 16,
        exclude_background: bool = True,
        top_n: int = 64,
    ) -> dict[str, Any]:
        """Compute a quantized RGB color histogram for a PNG.

        Each channel is quantized into `bins` buckets, producing up to bins^3
        unique colors. Colors are reported as the bucket centers in hex.

        Args:
            png_path: Absolute path to a PNG file.
            bins: Bucket count per channel (2-64, default 16). Total possible
                colors = bins^3.
            exclude_background: If True, drop transparent and inferred
                background pixels before counting (default True).
            top_n: Cap on number of entries returned, sorted by count desc
                (default 64).

        Returns:
            {bins, total_pixels, counted_pixels, entries: [{color_hex,
            pixel_count, fraction}, ...]}
        """
        if bins < 2 or bins > 64:
            return {"error": "bins must be between 2 and 64"}

        try:
            arr = _load_rgba(png_path)
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to load image: {e}"}

        h, w = arr.shape[:2]
        rgb = arr[:, :, :3]

        if exclude_background:
            mask = _foreground_mask(arr, _ALPHA_THRESHOLD_DEFAULT)
            pixels = rgb[mask]
        else:
            pixels = rgb.reshape(-1, 3)

        counted = int(pixels.shape[0])
        if counted == 0:
            return {
                "bins": bins,
                "total_pixels": h * w,
                "counted_pixels": 0,
                "entries": [],
            }

        # Quantize: bucket index = floor(value * bins / 256), clamped to bins-1.
        quant = np.minimum((pixels.astype(np.int32) * bins) // 256, bins - 1)
        # Pack (r, g, b) bucket indices into a single int for counting.
        keys = quant[:, 0] * bins * bins + quant[:, 1] * bins + quant[:, 2]
        unique, counts = np.unique(keys, return_counts=True)

        order = np.argsort(-counts)
        unique = unique[order][:top_n]
        counts = counts[order][:top_n]

        # Bucket center -> 0-255 representative color.
        step = 256.0 / bins
        entries = []
        for key, count in zip(unique.tolist(), counts.tolist()):
            r_idx, rem = divmod(key, bins * bins)
            g_idx, b_idx = divmod(rem, bins)
            r = min(int((r_idx + 0.5) * step), 255)
            g = min(int((g_idx + 0.5) * step), 255)
            b = min(int((b_idx + 0.5) * step), 255)
            entries.append(
                {
                    "color_hex": f"#{r:02x}{g:02x}{b:02x}",
                    "pixel_count": count,
                    "fraction": round(count / counted, 6),
                }
            )

        return {
            "bins": bins,
            "total_pixels": h * w,
            "counted_pixels": counted,
            "entries": entries,
        }

    @mcp.tool()
    def image_perceptual_hash(png_path: str) -> dict[str, Any]:
        """Compute a 64-bit dHash for a PNG.

        dHash compares adjacent pixels of a downsampled 9x8 grayscale version
        of the image. Hamming distance between two hashes approximates visual
        similarity (0 = identical structure, ~10 = clearly different).

        Args:
            png_path: Absolute path to a PNG file.

        Returns:
            {hash_hex, algorithm, bits}
        """
        try:
            arr = _load_rgba(png_path)
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to load image: {e}"}

        h = _dhash(arr)
        return {
            "hash_hex": f"{h:016x}",
            "algorithm": "dhash",
            "bits": 64,
        }

    @mcp.tool()
    def image_structural_diff(
        png_path_a: str,
        png_path_b: str,
        channel_diff_threshold: int = 5,
    ) -> dict[str, Any]:
        """Localise where two same-size PNGs differ.

        Complements image_compare. image_compare summarises global similarity
        (SSIM, mean diff, perceptual hash). This tool answers "where did a
        specific small feature change?" — useful for verifying primitive
        renders where the changed region is < 1% of the canvas and the global
        metrics are too coarse to be diagnostic.

        A pixel counts as different when the sum of absolute per-channel RGB
        deltas (0-765) exceeds ``channel_diff_threshold``. Refuses mismatched
        dimensions rather than resizing.

        Args:
            png_path_a: Absolute path to first PNG.
            png_path_b: Absolute path to second PNG.
            channel_diff_threshold: Channel-sum delta above which a pixel
                counts as different. Default 5 (i.e. ignore JPEG-ish noise
                under ~1.5 levels per channel).

        Returns:
            {
              "diff_pixel_count": int,
              "diff_bbox": {"x", "y", "width", "height"} | None,
              "mean_diff_in_bbox": float (0-255, RGB mean abs delta within
                                          the bbox, NaN-safe),
              "centroid": {"x", "y"} | None,
              "total_pixels": int,
              "diff_fraction": float,
              "width": int,
              "height": int,
              "threshold": int,
            }
            On error (mismatched dims, unreadable file):
              {"error": <reason>}
        """
        try:
            a = _load_rgba(png_path_a)
            b = _load_rgba(png_path_b)
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to load image: {e}"}

        if a.shape[:2] != b.shape[:2]:
            return {
                "error": (
                    f"Image dimensions differ: "
                    f"{a.shape[1]}x{a.shape[0]} vs {b.shape[1]}x{b.shape[0]}"
                ),
            }

        h, w = a.shape[:2]
        rgb_a = a[:, :, :3].astype(np.int16)
        rgb_b = b[:, :, :3].astype(np.int16)
        delta = np.abs(rgb_a - rgb_b).sum(axis=2)  # (H, W) int16, 0..765
        mask = delta > channel_diff_threshold
        total = int(h * w)
        count = int(mask.sum())

        if count == 0:
            return {
                "diff_pixel_count": 0,
                "diff_bbox": None,
                "mean_diff_in_bbox": 0.0,
                "centroid": None,
                "total_pixels": total,
                "diff_fraction": 0.0,
                "width": w,
                "height": h,
                "threshold": int(channel_diff_threshold),
            }

        ys, xs = np.where(mask)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        # Mean per-channel abs delta restricted to the bbox, on the original
        # RGB delta (not the channel-sum), so values stay in 0..255.
        bbox_rgb_delta = np.abs(rgb_a[y0:y1 + 1, x0:x1 + 1] - rgb_b[y0:y1 + 1, x0:x1 + 1])
        mean_in_bbox = float(bbox_rgb_delta.mean())

        return {
            "diff_pixel_count": count,
            "diff_bbox": {
                "x": x0,
                "y": y0,
                "width": x1 - x0 + 1,
                "height": y1 - y0 + 1,
            },
            "mean_diff_in_bbox": round(mean_in_bbox, 6),
            "centroid": {
                "x": int(round(float(xs.mean()))),
                "y": int(round(float(ys.mean()))),
            },
            "total_pixels": total,
            "diff_fraction": round(count / total, 6),
            "width": w,
            "height": h,
            "threshold": int(channel_diff_threshold),
        }

    @mcp.tool()
    def image_compare(png_path_a: str, png_path_b: str) -> dict[str, Any]:
        """Compare two PNGs of identical dimensions.

        Computes SSIM (structural similarity, 0-1 where 1 = identical), mean
        per-pixel absolute difference across RGB channels (0-255), and Hamming
        distance between dHashes (0-64).

        Args:
            png_path_a: Absolute path to first PNG.
            png_path_b: Absolute path to second PNG.

        Returns:
            {ssim_score, mean_pixel_diff, perceptual_hash_distance,
            hash_a, hash_b, width, height}
            Or {error} if the images have different dimensions or can't be
            loaded.
        """
        try:
            a = _load_rgba(png_path_a)
            b = _load_rgba(png_path_b)
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Failed to load image: {e}"}

        if a.shape[:2] != b.shape[:2]:
            return {
                "error": (
                    f"Image dimensions differ: "
                    f"{a.shape[1]}x{a.shape[0]} vs {b.shape[1]}x{b.shape[0]}"
                ),
            }

        rgb_a = a[:, :, :3].astype(np.int16)
        rgb_b = b[:, :, :3].astype(np.int16)
        mean_diff = float(np.abs(rgb_a - rgb_b).mean())

        gray_a = np.array(Image.fromarray(a[:, :, :3]).convert("L"), dtype=np.uint8)
        gray_b = np.array(Image.fromarray(b[:, :, :3]).convert("L"), dtype=np.uint8)
        ssim_score = float(ssim(gray_a, gray_b, data_range=255))

        ha, hb = _dhash(a), _dhash(b)

        return {
            "ssim_score": round(ssim_score, 6),
            "mean_pixel_diff": round(mean_diff, 6),
            "perceptual_hash_distance": _hamming(ha, hb),
            "hash_a": f"{ha:016x}",
            "hash_b": f"{hb:016x}",
            "width": int(a.shape[1]),
            "height": int(a.shape[0]),
        }
