"""Load and colourise a complete Sentinel-2 tile (no clipping, no mosaicking).

Each L2A band is a cloud-optimised GeoTIFF at the tile's native 10 m grid of
10980 x 10980 px = 120.56 MP. Reading the signed asset hrefs directly with
rasterio avoids any reprojection and keeps peak RAM low (~1 band at a time).
"""
from __future__ import annotations

import numpy as np
import rasterio

from .indices import dn_to_reflectance

BANDS = ["B04", "B03", "B02"]  # red, green, blue


def _sample_limits(
    hrefs: dict[str, str],
    item,
    *,
    low: float = 2.0,
    high: float = 98.0,
    per_band: bool = False,
) -> tuple[int, int, list[tuple[float, float]]]:
    """Read fast COG overviews (~1/10 scale) to get shape and stretch bounds."""
    samples: list[np.ndarray] = []
    height = width = 0
    for band in BANDS:
        with rasterio.open(hrefs[band]) as src:
            if height == 0:
                height, width = src.height, src.width
            oh = max(1, src.height // 10)
            ow = max(1, src.width // 10)
            ov = src.read(1, out_shape=(oh, ow))
        refl = dn_to_reflectance(ov, item=item)
        samples.append(refl[np.isfinite(refl)])

    if per_band:
        limits = []
        for vals in samples:
            if vals.size == 0:
                limits.append((0.0, 1.0))
            else:
                lo, hi = np.percentile(vals, [low, high])
                limits.append((float(lo), float(hi)))
        return height, width, limits

    combined = np.concatenate([v for v in samples if v.size > 0]) if any(v.size > 0 for v in samples) else np.array([])
    if combined.size == 0:
        joint = (0.0, 1.0)
    else:
        lo, hi = np.percentile(combined, [low, high])
        joint = (float(lo), float(hi))
    return height, width, [joint] * len(BANDS)


def load_fulltile_rgb(
    item,
    gamma: float = 0.95,
    *,
    per_band: bool = False,
) -> np.ndarray:
    """Return a (10980, 10980, 3) uint8 RGB array for a full tile."""
    hrefs = {band: item.assets[band].href for band in BANDS}
    height, width, limits = _sample_limits(hrefs, item, per_band=per_band)
    out = np.empty((height, width, 3), dtype=np.uint8)

    for i, (band, (lo, hi)) in enumerate(zip(BANDS, limits)):
        with rasterio.open(hrefs[band]) as src:
            arr = dn_to_reflectance(src.read(1), item=item)
        arr -= lo
        arr /= hi - lo + 1e-6
        np.clip(arr, 0.0, 1.0, out=arr)
        np.nan_to_num(arr, nan=0.0, copy=False)
        out[..., i] = (arr**gamma * 255).astype("uint8")
        del arr

    return out
