"""Load and colourise a complete Sentinel-2 tile (no clipping, no mosaicking).

Each L2A band is a cloud-optimised GeoTIFF at the tile's native 10 m grid of
10980 x 10980 px = 120.56 MP. Reading the signed asset hrefs directly with
rasterio avoids any reprojection and keeps memory predictable.
"""
from __future__ import annotations

import numpy as np
import rasterio

BANDS = ["B04", "B03", "B02"]  # red, green, blue


def load_fulltile_rgb(item, gamma: float = 0.95) -> np.ndarray:
    """Return a (10980, 10980, 3) uint8 RGB array for a full tile."""
    hrefs = {band: item.assets[band].href for band in BANDS}

    with rasterio.open(hrefs["B04"]) as src:
        height, width = src.height, src.width
    out = np.empty((height, width, 3), dtype=np.uint8)

    for i, band in enumerate(BANDS):
        with rasterio.open(hrefs[band]) as src:
            arr = src.read(1).astype("float32") / 10_000.0
        arr[arr <= 0] = np.nan  # L2A nodata

        # Estimate the stretch range from a cheap ~1.2 M pixel sample.
        sample = arr[::10, ::10]
        lo, hi = np.nanpercentile(sample, [2.0, 98.0])
        arr -= lo
        arr /= hi - lo + 1e-6
        np.clip(arr, 0.0, 1.0, out=arr)
        np.nan_to_num(arr, nan=0.0, copy=False)
        out[..., i] = (arr**gamma * 255).astype("uint8")
        del arr

    return out
