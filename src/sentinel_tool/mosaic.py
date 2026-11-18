"""Build a cloudless, ~30 MP true-colour image of a fixed AOI.

Sentinel-2 tiles are 110 km squares but a 60 x 50 km AOI can straddle a tile
boundary, so one scene may not cover it. We greedily pick the *minimum number*
of near-cloudless scenes whose footprints cover the AOI, then composite them
pixel-by-pixel (with SCL cloud + nodata masking). Native 10 m bands B04/B03/B02
give ~6000 x 5000 px = 30 MP over the AOI.
"""
from __future__ import annotations

import numpy as np
from shapely.geometry import GeometryCollection, box, shape

from .config import S2_CLOUD_SCL, utm_epsg
from .indices import dn_to_reflectance

TRUE_COLOR = ["B04", "B03", "B02"]  # red, green, blue
CLOUD_SCL = list(S2_CLOUD_SCL)


def pick_scenes(items, bbox, cloud_max: float = 1.0, target_cover: float = 0.995):
    """Greedily select the fewest clear scenes whose footprints cover the AOI.

    Returns (selected_items, coverage_fraction).
    """
    items = list(items)
    if not items:
        return [], 0.0

    aoi = box(*bbox)
    candidates = []
    for item in items:
        raw_cloud = item.properties.get("eo:cloud_cover")
        cloud = float(raw_cloud) if raw_cloud is not None else 999.0
        if cloud > cloud_max or not item.geometry:
            continue
        candidates.append((cloud, shape(item.geometry), item))

    if not candidates:
        # Fall back to the single clearest scene whatever its cloud cover.
        best = min(
            items,
            key=lambda i: float(i.properties["eo:cloud_cover"])
            if i.properties.get("eo:cloud_cover") is not None
            else 999.0,
        )
        cov = shape(best.geometry).intersection(aoi).area / aoi.area if best.geometry else 0.0
        return [best], cov

    selected: list = []
    covered = GeometryCollection()
    while True:
        best_gain, best = 0.0, None
        for cloud, geom, item in candidates:
            if item in selected:
                continue
            gain = geom.difference(covered).intersection(aoi).area
            if gain > best_gain or (
                gain == best_gain and gain > 0 and (best is None or cloud < best[0])
            ):
                best_gain, best = gain, (cloud, geom, item)
        if best is None or best_gain <= 1e-6:
            break
        selected.append(best[2])
        covered = covered.union(best[1])
        if covered.intersection(aoi).area / aoi.area >= target_cover:
            break

    coverage = covered.intersection(aoi).area / aoi.area
    return selected, coverage


def load_scene_rgb(item, bbox, resolution: int = 10):
    """Load B04/B03/B02 for one scene, masking clouds/cirrus/nodata as NaN.

    Returns float32 array (y, x, 3) in surface reflectance units (PB 04.00+ corrected).
    """
    import stackstac

    lon_center = (bbox[0] + bbox[2]) / 2.0
    lat_center = (bbox[1] + bbox[3]) / 2.0
    epsg = utm_epsg(lon_center, lat_center)
    cube = stackstac.stack(
        [item],
        assets=TRUE_COLOR + ["SCL"],
        bounds_latlon=bbox,
        epsg=epsg,
        resolution=resolution,
        chunksize=2048,
    )
    scene = cube.isel(time=0).compute()
    scl = scene.sel(band="SCL")
    cloud_mask = scl.isin(CLOUD_SCL)

    rgb = dn_to_reflectance(scene.sel(band=TRUE_COLOR), item=item)
    rgb = rgb.where(~cloud_mask).transpose("y", "x", "band")
    return rgb.values.astype("float32")


def composite(scenes_rgb: list[np.ndarray]) -> np.ndarray:
    """Fill each pixel from the first scene that has valid (non-NaN) data."""
    if not scenes_rgb:
        raise ValueError("scenes_rgb must not be empty")
    out = np.full_like(scenes_rgb[0], np.nan, dtype="float32")
    for arr in scenes_rgb:
        if arr.shape != out.shape:
            raise ValueError("scene arrays have mismatched shapes")
        fill = np.isnan(out) & np.isfinite(arr)
        out[fill] = arr[fill]
    return out


def stretch(
    rgb: np.ndarray,
    low: float = 2.0,
    high: float = 98.0,
    gamma: float = 1.0,
    *,
    per_band: bool = False,
) -> np.ndarray:
    """Percentile stretch to 0-255 uint8 with optional gamma.

    By default computes a joint [low, high] range across all channels to
    preserve natural white balance over deserts, salt flats, and water.
    Pass per_band=True for independent per-channel stretching.
    """
    arr = np.asarray(rgb, dtype="float32")
    out = np.zeros(arr.shape, dtype="uint8")

    if not per_band:
        valid = arr[np.isfinite(arr)]
        if valid.size == 0:
            return out
        lo, hi = np.percentile(valid, [low, high])
        norm = np.clip((arr - lo) / (hi - lo + 1e-6), 0.0, 1.0)
        norm = np.nan_to_num(norm, nan=0.0)
        return (norm**gamma * 255).astype("uint8")

    for b in range(arr.shape[-1]):
        chan = arr[..., b]
        valid = chan[np.isfinite(chan)]
        if valid.size == 0:
            continue
        lo, hi = np.percentile(valid, [low, high])
        norm = np.clip((chan - lo) / (hi - lo + 1e-6), 0.0, 1.0)
        norm = np.nan_to_num(norm, nan=0.0)
        out[..., b] = (norm**gamma * 255).astype("uint8")
    return out
